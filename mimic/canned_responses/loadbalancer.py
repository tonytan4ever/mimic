# -*- test-case-name: mimic.test.test_loadbalancer -*-
"""
Canned response for add/get/list/delete load balancers and
add/get/delete/list nodes
"""
from random import randrange
from datetime import datetime
from copy import deepcopy
from mimic.util.helper import (not_found_response, invalid_resource,
                               set_resource_status, fmt as time_format)
from twisted.python import log


class CLB_Cache(object):
    """
    Object that stores a cache of CLB and CLB Metadata info
    """
    def __init__(self):
        """
        There are two caches - the lb info, and the metadata info
        """
        self.lbs = {}
        self.meta = {}


def load_balancer_example(lb_info, lb_id, status,
                          current_time):
    """
    Create load balancer response example
    """
    lb_example = {"name": lb_info["name"],
                  "id": lb_id,
                  "protocol": lb_info["protocol"],
                  "port": lb_info.get("port", 80),
                  "algorithm": lb_info.get("algorithm") or "RANDOM",
                  "status": status,
                  "cluster": {"name": "test-cluster"},
                  "timeout": lb_info.get("timeout", 30),
                  "created": {"time": current_time},
                  "virtualIps": [{"address": "127.0.0.1",
                                 "id": 1111, "type": "PUBLIC", "ipVersion": "IPV4"},
                                 {"address": "0000:0000:0000:0000:1111:111b:0000:0000",
                                  "id": 1111,
                                  "type": "PUBLIC",
                                  "ipVersion": "IPV6"}],
                  "sourceAddresses": {"ipv6Public": "0000:0001:0002::00/00",
                                      "ipv4Servicenet": "127.0.0.1",
                                      "ipv4Public": "127.0.0.1"},
                  "httpsRedirect": lb_info.get("httpsRedirect", False),
                  "updated": {"time": current_time},
                  "halfClosed": lb_info.get("halfClosed", False),
                  "connectionLogging": lb_info.get("connectionLogging", {"enabled": False}),
                  "contentCaching": {"enabled": False}}
    if lb_info.get("nodes"):
        lb_example.update({"nodes": _format_nodes_on_lb(lb_info["nodes"])})
    if lb_info.get("metadata"):
        lb_example.update({"metadata": _format_meta(lb_info["metadata"])})
    return lb_example


def add_load_balancer(tenant_id, cache, lb_info, lb_id, current_timestamp):
    """
    Returns response of a newly created load balancer with
    response code 202, and adds the new lb to the cache's lbs.
    Note: ``cache.lbs`` has tenant_id added as an extra key in comparison
    to the lb_example.
    """
    status = "ACTIVE"

    # Loadbalancers metadata is a list object, creating a metadata cache
    # so we dont have to deal with the list
    meta = {}
    if "metadata" in lb_info:
        for each in lb_info["metadata"]:
            meta.update({each["key"]: each["value"]})
    cache.meta[lb_id] = meta
    log.msg(cache.meta)

    if "lb_building" in cache.meta[lb_id]:
        status = "BUILD"

    # Add tenant_id and nodeCount to cache.lbs
    current_timestring = datetime.utcfromtimestamp(current_timestamp).strftime(time_format)
    cache.lbs[lb_id] = load_balancer_example(lb_info, lb_id, status,
                                             current_timestring)
    cache.lbs[lb_id].update({"tenant_id": tenant_id})
    cache.lbs[lb_id].update(
        {"nodeCount": len(cache.lbs[lb_id].get("nodes", []))})

    # and remove before returning response for add lb
    new_lb = _lb_without_tenant(cache, lb_id)

    return {'loadBalancer': new_lb}, 202


def get_load_balancers(cache, lb_id, current_timestamp):
    """
    Returns the load balancers with the given lb id, with response
    code 200. If no load balancers are found returns 404.
    """
    if lb_id in cache.lbs:
        _verify_and_update_lb_state(cache, lb_id, False, current_timestamp)
        log.msg(cache.lbs[lb_id]["status"])
        new_lb = _lb_without_tenant(cache, lb_id)
        return {'loadBalancer': new_lb}, 200
    return not_found_response("loadbalancer"), 404


def del_load_balancer(cache, lb_id, current_timestamp):
    """
    Returns response for a load balancer that is in building status for 20
    seconds and response code 202, and adds the new lb to ``cache.lbs``.
    A loadbalancer, on delete, goes into PENDING-DELETE and remains in DELETED
    status until a nightly job(maybe?)
    """
    if lb_id in cache.lbs:

        if cache.lbs[lb_id]["status"] == "PENDING-DELETE":
            msg = ("Must provide valid load balancers: {0} are immutable and "
                   "could not be processed.".format(lb_id))
            # Dont doubt this to be 422, it is 400!
            return invalid_resource(msg, 400), 400

        _verify_and_update_lb_state(cache, lb_id, True, current_timestamp)

        if any([cache.lbs[lb_id]["status"] == "ACTIVE",
                cache.lbs[lb_id]["status"] == "ERROR",
                cache.lbs[lb_id]["status"] == "PENDING-UPDATE"]):
            del cache.lbs[lb_id]
            return b'', 202

        if cache.lbs[lb_id]["status"] == "PENDING-DELETE":
            return b'', 202

        if cache.lbs[lb_id]["status"] == "DELETED":
            _verify_and_update_lb_state(cache, lb_id,
                                        current_timestamp=current_timestamp)
            msg = "Must provide valid load balancers: {0} could not be found.".format(lb_id)
            # Dont doubt this to be 422, it is 400!
            return invalid_resource(msg, 400), 400

    return not_found_response("loadbalancer"), 404


def list_load_balancers(tenant_id, cache, current_timestamp):
    """
    Returns the list of load balancers with the given tenant id with response
    code 200. If no load balancers are found returns empty list.
    """
    response = dict(
        (k, v) for (k, v) in cache.lbs.items()
        if tenant_id == v['tenant_id']
    )
    for each in response:
        _verify_and_update_lb_state(cache, each, False, current_timestamp)
        log.msg(cache.lbs[each]["status"])
    updated_resp = dict(
        (k, v) for (k, v) in cache.lbs.items()
        if tenant_id == v['tenant_id']
    )
    return {'loadBalancers': _prep_for_list(updated_resp.values()) or []}, 200


def add_node(cache, node_list, lb_id, current_timestamp):
    """
    Returns the canned response for add nodes
    """
    if lb_id in cache.lbs:

        _verify_and_update_lb_state(cache, lb_id, False, current_timestamp)

        if cache.lbs[lb_id]["status"] != "ACTIVE":
            resource = invalid_resource(
                "Load Balancer '{0}' has a status of {1} and is considered "
                "immutable.".format(lb_id, cache.lbs[lb_id]["status"]), 422)
            return (resource, 422)

        nodes = _format_nodes_on_lb(node_list)

        if cache.lbs[lb_id].get("nodes"):
            for existing_node in cache.lbs[lb_id]["nodes"]:
                for new_node in node_list:
                    if (existing_node["address"] == new_node["address"] and
                            existing_node["port"] == new_node["port"]):
                        resource = invalid_resource(
                            "Duplicate nodes detected. One or more nodes "
                            "already configured on load balancer.", 413)
                        return (resource, 413)

            cache.lbs[lb_id]["nodes"] = cache.lbs[lb_id]["nodes"] + nodes
        else:
            cache.lbs[lb_id]["nodes"] = nodes
            cache.lbs[lb_id]["nodeCount"] = len(cache.lbs[lb_id]["nodes"])
            _verify_and_update_lb_state(cache, lb_id,
                                        current_timestamp=current_timestamp)
        return {"nodes": nodes}, 200

    return not_found_response("loadbalancer"), 404


def get_nodes(cache, lb_id, node_id, current_timestamp):
    """
    Returns the node on the load balancer
    """
    if lb_id in cache.lbs:
        _verify_and_update_lb_state(cache, lb_id, False, current_timestamp)

        if cache.lbs[lb_id]["status"] == "DELETED":
            return (
                invalid_resource(
                    "The loadbalancer is marked as deleted.", 410),
                410)

        if cache.lbs[lb_id].get("nodes"):
            for each in cache.lbs[lb_id]["nodes"]:
                if node_id == each["id"]:
                    return {"node": each}, 200
        return not_found_response("node"), 404

    return not_found_response("loadbalancer"), 404


def delete_node(cache, lb_id, node_id, current_timestamp):
    """
    Determines whether the node to be deleted exists in mimic cache and
    returns the response code.
    """
    if lb_id in cache.lbs:

        _verify_and_update_lb_state(cache, lb_id, False, current_timestamp)

        if cache.lbs[lb_id]["status"] != "ACTIVE":
            resource = invalid_resource(
                "Load Balancer '{0}' has a status of {1} and is considered "
                "immutable.".format(lb_id, cache.lbs[lb_id]["status"]), 422)
            return (resource, 422)

        _verify_and_update_lb_state(cache, lb_id,
                                    current_timestamp=current_timestamp)

        if cache.lbs[lb_id].get("nodes"):
            for each in cache.lbs[lb_id]["nodes"]:
                if each["id"] == node_id:
                    index = cache.lbs[lb_id]["nodes"].index(each)
                    del cache.lbs[lb_id]["nodes"][index]
                    if not cache.lbs[lb_id]["nodes"]:
                        del cache.lbs[lb_id]["nodes"]
                    cache.lbs[lb_id].update({"nodeCount": len(cache.lbs[lb_id].get("nodes", []))})
                    return None, 202

        return not_found_response("node"), 404

    return not_found_response("loadbalancer"), 404


def list_nodes(cache, lb_id, current_timestamp):
    """
    Returns the list of nodes remaining on the load balancer
    """
    if lb_id in cache.lbs:
        _verify_and_update_lb_state(cache, lb_id, False, current_timestamp)
        if lb_id not in cache.lbs:
            return not_found_response("loadbalancer"), 404

        if cache.lbs[lb_id]["status"] == "DELETED":
            return invalid_resource("The loadbalancer is marked as deleted.", 410), 410
        node_list = []
        if cache.lbs[lb_id].get("nodes"):
            node_list = cache.lbs[lb_id]["nodes"]
        return {"nodes": node_list}, 200
    else:
        return not_found_response("loadbalancer"), 404


def _format_nodes_on_lb(node_list):
    """
    create a dict of nodes given the list of nodes
    """
    nodes = []
    for each in node_list:
        node = {}
        node["address"] = each["address"]
        node["condition"] = each["condition"]
        node["port"] = each["port"]
        if each.get("weight"):
            node["weight"] = each["weight"]
        if each.get("type"):
            node["type"] = each["type"]
        node["id"] = randrange(999999)
        node["status"] = "ONLINE"
        nodes.append(node)
    return nodes


def _format_meta(metadata_list):
    """
    creates metadata with 'id' as a key
    """
    meta = []
    for each in metadata_list:
        each.update({"id": randrange(999)})
        meta.append(each)
    return meta


def _lb_without_tenant(cache, lb_id):
    """
    returns a copy of the cache for the given lb_id, without
    tenant_id
    """
    new_lb = deepcopy(cache.lbs[lb_id])
    del new_lb["tenant_id"]
    del new_lb["nodeCount"]
    return new_lb


def _prep_for_list(lb_list):
    """
    Removes tenant id and changes the nodes list to 'nodeCount' set to the
    number of node on the LB
    """
    entries_to_keep = ('name', 'protocol', 'id', 'port', 'algorithm', 'status', 'timeout',
                       'created', 'virtualIps', 'updated', 'nodeCount')
    filtered_lb_list = []
    for each in lb_list:
        filtered_lb_list.append(dict((entry, each[entry]) for entry in entries_to_keep))
    return filtered_lb_list


def _verify_and_update_lb_state(cache, lb_id, set_state=True,
                                current_timestamp=None):
    """
    Based on the current state, the metadata on the lb and the time since the LB has
    been in that state, set the appropriate state in cache.lbs
    Note: Reconsider if update metadata is implemented
    """
    current_timestring = datetime.utcfromtimestamp(current_timestamp).strftime(time_format)
    if cache.lbs[lb_id]["status"] == "BUILD":
        cache.meta[lb_id]["lb_building"] = cache.meta[lb_id]["lb_building"] or 10
        cache.lbs[lb_id]["status"] = set_resource_status(
            cache.lbs[lb_id]["updated"]["time"],
            cache.meta[lb_id]["lb_building"],
            current_timestamp=current_timestamp
        ) or "BUILD"

    elif cache.lbs[lb_id]["status"] == "ACTIVE" and set_state:
        if "lb_pending_update" in cache.meta[lb_id]:
            cache.lbs[lb_id]["status"] = "PENDING-UPDATE"
            log.msg("here")
            log.msg(cache.lbs[lb_id]["status"])
        if "lb_pending_delete" in cache.meta[lb_id]:
            cache.lbs[lb_id]["status"] = "PENDING-DELETE"
        if "lb_error_state" in cache.meta[lb_id]:
            cache.lbs[lb_id]["status"] = "ERROR"
        cache.lbs[lb_id]["updated"]["time"] = current_timestring

    elif cache.lbs[lb_id]["status"] == "PENDING-UPDATE":
        if "lb_pending_update" in cache.meta[lb_id]:
            cache.lbs[lb_id]["status"] = set_resource_status(
                cache.lbs[lb_id]["updated"]["time"],
                cache.meta[lb_id]["lb_pending_update"],
                current_timestamp=current_timestamp
            ) or "PENDING-UPDATE"

    elif cache.lbs[lb_id]["status"] == "PENDING-DELETE":
        cache.meta[lb_id]["lb_pending_delete"] = cache.meta[lb_id]["lb_pending_delete"] or 10
        cache.lbs[lb_id]["status"] = set_resource_status(
            cache.lbs[lb_id]["updated"]["time"],
            cache.meta[lb_id]["lb_pending_delete"], "DELETED",
            current_timestamp=current_timestamp
        ) or "PENDING-DELETE"
        cache.lbs[lb_id]["updated"]["time"] = current_timestring

    elif cache.lbs[lb_id]["status"] == "DELETED":
        # see del_load_balancer above for an explanation of this state change.
        cache.lbs[lb_id]["status"] = set_resource_status(
            cache.lbs[lb_id]["updated"]["time"], 3600, "DELETING-NOW",
            current_timestamp=current_timestamp
        ) or "DELETED"
        if cache.lbs[lb_id]["status"] == "DELETING-NOW":
            del cache.lbs[lb_id]
