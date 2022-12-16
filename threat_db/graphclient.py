#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import functools
import os

import requests
from gql import Client, gql, transport
from gql.dsl import DSLMutation, DSLSchema, dsl_gql
from gql.transport.exceptions import TransportQueryError
from gql.transport.requests import RequestsHTTPTransport
from gql.transport.requests import log as requests_logger
from urllib3.exceptions import MaxRetryError

from threat_db.logger import LOG, WARNING
from threat_db.queries import drop_all_query, health_query, introspect_query
from threat_db.schema import graphql_schema

requests_logger.setLevel(WARNING)

headers = {"Content-Type": "application/json", "Accept-Encoding": "gzip"}

# Add access tokens to the requests
# The kind people at dgraph use several different headers for tokens!
if os.getenv("DGRAPH_API_KEY"):
    headers["X-Dgraph-AuthToken"] = os.getenv("DGRAPH_API_KEY")
    headers["X-Auth-Token"] = os.getenv("DGRAPH_API_KEY")
if os.getenv("DGRAPH_CLOUD_API_KEY"):
    headers["Authorization"] = f"""Bearer {os.getenv("DGRAPH_CLOUD_API_KEY")}"""
if os.getenv("DGRAPH_ACL_KEY"):
    headers["X-Dgraph-AccessToken"] = os.getenv("DGRAPH_ACL_KEY")


def catch_db_errors(fn):
    @functools.wraps(fn)
    def caller(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except requests.exceptions.ConnectionError:
            LOG.warn("GraphQL API is unavailable")
        except MaxRetryError:
            LOG.warn("GraphQL and admin API are currently unavailable")
        except TransportQueryError as tqe:
            if tqe.errors:
                first_error = tqe.errors[0].get("message", "")
                try:
                    # dgraph doesn't support multiple concurrent mutations. So we workaround by retrying
                    # See https://discuss.dgraph.io/t/transactions-in-graphql/6861
                    if (
                        "couldn't commit transaction" in first_error
                        and "Please retry" in first_error
                    ):
                        LOG.info("Retrying failed mutation")
                        return fn(*args, **kwargs)
                except Exception as e:
                    print(e)
        except Exception as ex:
            LOG.exception(ex)

    return caller


def process_query_response(res):
    json_resp = res.json()
    if isinstance(json_resp, dict) and json_resp.get("errors"):
        errors = json_resp.get("errors")
        if len(errors):
            first_error = errors[0].get("message")
            if "No Auth Token" in first_error:
                LOG.warn(
                    f"Please set the database authentication token via the environment variable DGRAPH_API_KEY"
                )
        LOG.error(json_resp.get("errors"))
        return json_resp.get("errors")
    elif isinstance(json_resp, list):
        return json_resp
    return json_resp.get("data")


# Drop All - discard all data and start from a clean slate.
def drop_all(client, host):
    if host.endswith("graphql"):
        host = host.replace("/graphql", "") + "/alter"
    res = requests.post(host, data=drop_all_query, headers=headers)
    if res.ok:
        return process_query_response(res)
    LOG.warn(f"Drop all data failed due to {res.status_code} {host}")
    return None


def healthcheck(client, host):
    if host.endswith("graphql"):
        host = host.replace("/graphql", "") + "/health"
    res = requests.post(host, data=health_query, headers=headers)
    if res.ok:
        return process_query_response(res)
    LOG.warn(f"Unable to perform healthcheck due to {res.status_code} {host}")
    return None


def is_alive(client, host):
    health_res = healthcheck(client, host)
    for node in health_res:
        if node.get("instance") == "alpha" and node.get("status") == "healthy":
            return True
    return False


def create_schemas(client, host):
    needs_creation = True
    # Do not recreate schema unnecessarily
    try:
        with client as session:
            query = gql(introspect_query)
            result = session.execute(query)
            if result and result.get("__schema", {}).get("types"):
                needs_creation = False
                return result
    except requests.exceptions.ConnectionError:
        needs_creation = False
    except MaxRetryError:
        needs_creation = False
    except TransportQueryError as e:
        if e.errors:
            first_error = e.errors[0].get("message", "")
            needs_creation = "Not resolving __schema" in first_error
    except Exception as e:
        needs_creation = True
    if needs_creation:
        if host.endswith("graphql"):
            host = host.replace("/graphql", "") + "/admin/schema"
        res = requests.post(host, data=graphql_schema, headers=headers)
        if res.ok:
            return process_query_response(res)
        LOG.warn(f"Create schema failed due to {res.status_code} {host}")
        return None
    else:
        LOG.debug("Database schema already exists")
        return ""


@catch_db_errors
def create_bom(client, bom):
    with client as session:
        ds = DSLSchema(client.schema)
        query = dsl_gql(
            DSLMutation(
                ds.Mutation.addBom(input=bom, upsert=True).select(
                    ds.AddBomPayload.bom.select(ds.Bom.serialNumber)
                )
            )
        )
        result = session.execute(query)
        return result


@catch_db_errors
def create_components(client, components):
    with client as session:
        ds = DSLSchema(client.schema)
        query = dsl_gql(
            DSLMutation(
                ds.Mutation.addComponent(input=components, upsert=True).select(
                    ds.AddComponentPayload.component.select(
                        ds.Component.purl, ds.Component.bomRef
                    )
                )
            )
        )
        result = session.execute(query)
        return result


@catch_db_errors
def create_vulns(client, vulns):
    with client as session:
        ds = DSLSchema(client.schema)
        query = dsl_gql(
            DSLMutation(
                ds.Mutation.addVulnerability(input=vulns, upsert=True).select(
                    ds.AddVulnerabilityPayload.vulnerability.select(
                        ds.Vulnerability.id,
                        ds.Vulnerability.bomRef,
                        ds.Vulnerability.version,
                    )
                )
            )
        )
        result = session.execute(query)
        return result


def get(host, api_key=None):
    if not host.endswith("graphql"):
        host = f"{host}/graphql"
    if api_key:
        headers["Authorization"] = api_key
    transport = RequestsHTTPTransport(url=host, verify=True, headers=headers, retries=3)
    client = Client(transport=transport, fetch_schema_from_transport=True)
    return transport, client
