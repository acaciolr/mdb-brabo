"""
collectors/security.py
Users/roles and the security-relevant startup options.

getCmdLineOpts is the only reliable way to know whether `authorization` and
TLS are actually ON (a user being authenticated proves nothing: the
localhost exception and auth-disabled servers both accept connections).
Without that privilege the panel says "unknown", never "off".

Cache keys: security.users, security.roles, security.options,
            security.mechanisms, security.available/.reason
"""

from __future__ import annotations

from mongodb_brabo.collectors.base import BaseCollector, short_error


def summarize_options(cmdline: dict | None) -> dict:
    if not cmdline:
        return {"known": False}
    p = cmdline.get("parsed") or {}
    sec = p.get("security") or {}
    net = p.get("net") or {}
    tls = net.get("tls") or net.get("ssl") or {}
    auth = sec.get("authorization")
    if auth is None and (sec.get("keyFile") or sec.get("clusterAuthMode") in ("keyFile", "x509")):
        auth = "enabled (implícito por keyFile)"
    return {
        "known": True,
        "authorization": auth or "disabled",
        "auth_enabled": bool(auth) and auth != "disabled",
        "key_file": bool(sec.get("keyFile")),
        "cluster_auth_mode": sec.get("clusterAuthMode", ""),
        "tls_mode": tls.get("mode", "disabled"),
        "bind_ip": net.get("bindIp", "") or ("ALL (bindIpAll)" if net.get("bindIpAll") else ""),
        "port": net.get("port"),
        "audit_log": bool(p.get("auditLog")),
        "javascript_enabled": (p.get("security") or {}).get("javascriptEnabled", True),
        "argv": " ".join(cmdline.get("argv") or []),
        "config_file": p.get("config", ""),
    }


class SecurityCollector(BaseCollector):
    def collect_sync(self) -> None:
        errors: dict[str, str] = {}
        cmdline = None
        if self.caps.can_cmdline_opts:
            try:
                cmdline = self.conn.command("admin", "getCmdLineOpts")
            except Exception as exc:
                errors["getCmdLineOpts"] = short_error(exc)
        else:
            errors["getCmdLineOpts"] = "sem privilégio getCmdLineOpts (clusterMonitor)"

        users: list[dict] = []
        roles: list[dict] = []
        if self.caps.can_view_users:
            try:
                res = self.conn.command("admin", {"usersInfo": {"forAllDBs": True}})
                for u in res.get("users") or []:
                    users.append({
                        "user": u.get("user"), "db": u.get("db"),
                        "roles": [f"{r.get('role')}@{r.get('db')}" for r in u.get("roles") or []],
                        "mechanisms": u.get("mechanisms") or [],
                    })
            except Exception as exc:
                errors["usersInfo"] = short_error(exc)
            try:
                res = self.conn.command("admin", {"rolesInfo": 1, "showPrivileges": False, "showBuiltinRoles": False})
                for r in res.get("roles") or []:
                    roles.append({"role": r.get("role"), "db": r.get("db"),
                                  "inherits": [f"{x.get('role')}@{x.get('db')}" for x in r.get("roles") or []]})
            except Exception as exc:
                errors["rolesInfo"] = short_error(exc)
        else:
            errors["usersInfo"] = "sem privilégio viewUser (userAdminAnyDatabase/root)"

        mechanisms = None
        try:
            mechanisms = self.conn.command("admin", {"getParameter": 1, "authenticationMechanisms": 1}).get(
                "authenticationMechanisms")
        except Exception as exc:
            errors["getParameter"] = short_error(exc)

        ttl = self.ttl
        self.available("security")
        self.cache.set("security.options", summarize_options(cmdline), ttl=ttl)
        self.cache.set("security.users", users, ttl=ttl)
        self.cache.set("security.roles", roles, ttl=ttl)
        self.cache.set("security.mechanisms", mechanisms, ttl=ttl)
        self.cache.set("security.errors", errors, ttl=ttl)
        self.cache.set("security.session", {"users": self.caps.users, "roles": self.caps.roles}, ttl=ttl)
