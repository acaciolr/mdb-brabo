from mongodb_brabo.core.capabilities import (TOPOLOGY_ARBITER, TOPOLOGY_CONFIGSVR, TOPOLOGY_MONGOS,
                                             TOPOLOGY_REPLSET, TOPOLOGY_STANDALONE, parse_capabilities,
                                             parse_version)

ROOT_STATUS = {"authInfo": {"authenticatedUsers": [{"user": "root_admin", "db": "admin"}],
                            "authenticatedUserRoles": [{"role": "root", "db": "admin"}]}}


def test_parse_version():
    assert parse_version("8.0.34") == (8, 0)
    assert parse_version("4.4.29-rc1") == (4, 4)
    assert parse_version("") == (0, 0)


def test_standalone_root():
    caps = parse_capabilities({"isWritablePrimary": True}, {"version": "8.0.34"}, ROOT_STATUS,
                              {"storageEngine": {"name": "wiredTiger"}})
    assert caps.topology == TOPOLOGY_STANDALONE
    assert caps.role_label == "STANDALONE"
    assert caps.all_privileges and caps.can_kill_op and caps.can_current_op
    assert not caps.can_repl_status and not caps.can_read_oplog and not caps.can_list_shards
    assert caps.has_wiredtiger and caps.tickets_in_queues


def test_replset_secondary_and_primary():
    sec = parse_capabilities({"setName": "rs0", "secondary": True, "isWritablePrimary": False},
                             {"version": "7.0.12"}, ROOT_STATUS)
    assert sec.topology == TOPOLOGY_REPLSET and sec.role_label == "SECONDARY"
    assert sec.can_repl_status and sec.can_read_oplog and not sec.can_step_down
    pri = parse_capabilities({"setName": "rs0", "isWritablePrimary": True}, {"version": "6.0.1"}, ROOT_STATUS)
    assert pri.role_label == "PRIMARY" and pri.can_step_down and not pri.tickets_in_queues


def test_mongos_arbiter_configsvr():
    m = parse_capabilities({"msg": "isdbgrid"}, {"version": "8.0.0"}, ROOT_STATUS)
    assert m.topology == TOPOLOGY_MONGOS and m.can_list_shards and not m.can_profile
    a = parse_capabilities({"setName": "rs1", "arbiterOnly": True}, {"version": "8.0.0"}, ROOT_STATUS)
    assert a.topology == TOPOLOGY_ARBITER and not a.can_read_oplog and a.role_label == "ARBITER"
    cfg = parse_capabilities({"setName": "cfg", "isWritablePrimary": True}, {"version": "8.0.0"}, ROOT_STATUS,
                             cmdline={"parsed": {"sharding": {"clusterRole": "configsvr"}}})
    assert cfg.topology == TOPOLOGY_CONFIGSVR and cfg.role_label.startswith("CONFIGSVR")


def test_cluster_monitor_privileges_are_read_only():
    status = {"authInfo": {
        "authenticatedUsers": [{"user": "mon", "db": "admin"}],
        "authenticatedUserRoles": [{"role": "clusterMonitor", "db": "admin"}],
        "authenticatedUserPrivileges": [
            {"resource": {"cluster": True}, "actions": ["serverStatus", "inprog", "replSetGetStatus",
                                                        "getCmdLineOpts", "listShards"]},
            {"resource": {"db": "", "collection": ""}, "actions": ["collStats", "dbStats", "indexStats"]},
        ]}}
    caps = parse_capabilities({"setName": "rs0", "isWritablePrimary": True}, {"version": "8.0.0"}, status)
    assert not caps.all_privileges
    assert caps.can_server_status and caps.can_current_op and caps.can_repl_status and caps.can_cmdline_opts
    assert not caps.can_kill_op and not caps.can_step_down and not caps.can_view_users and not caps.can_compact


def test_fallback_role_map_without_privileges():
    status = {"authInfo": {"authenticatedUsers": [{"user": "x", "db": "admin"}],
                           "authenticatedUserRoles": [{"role": "clusterMonitor", "db": "admin"}]}}
    caps = parse_capabilities({}, {"version": "4.0.0"}, status)
    assert caps.can_current_op and not caps.can_kill_op


def test_no_auth_means_optimistic():
    caps = parse_capabilities({}, {"version": "8.0.0"}, {"authInfo": {"authenticatedUsers": []}},
                              cmdline={"parsed": {"net": {"port": 27017}}})
    assert caps.all_privileges
    assert caps.auth_enabled is False


def test_auth_detection_from_cmdline():
    caps = parse_capabilities({}, {"version": "8.0.0"}, ROOT_STATUS,
                              cmdline={"parsed": {"security": {"keyFile": "/k"}}})
    assert caps.auth_enabled is True
