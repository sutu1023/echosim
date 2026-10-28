"""Synthetic local backup drill ONLY, invoked with a disposable Compose project."""

import base64
import json
import secrets
import subprocess
import time


def drill(compose, env, root, artifacts):
    def pg(*args, input=None):
        return subprocess.check_output(
            [*compose, "exec", "-T", "postgres", *args], cwd=root, env=env, input=input
        )

    started = time.monotonic()
    pg(
        "psql",
        "-U",
        "echosim_test",
        "-d",
        "echosim_test",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        "CREATE TABLE drill_records(id integer primary key, note text); "
        "INSERT INTO drill_records VALUES (1,'synthetic-only');",
    )
    data = pg("pg_dump", "-U", "echosim_test", "-Fc", "echosim_test")
    # The randomly generated encryption key stays in this pipe/memory, never an artifact.
    key = base64.b64encode(secrets.token_bytes(32)).decode()
    program = """
const crypto=require('node:crypto'),fs=require('node:fs'),assert=require('node:assert/strict');
const d=JSON.parse(fs.readFileSync(0,'utf8')),key=Buffer.from(d.key,'base64');
const raw=Buffer.from(d.dump,'base64'),iv=crypto.randomBytes(12);
const cipher=crypto.createCipheriv('aes-256-gcm',key,iv);
const encrypted=Buffer.concat([cipher.update(raw),cipher.final()]),tag=cipher.getAuthTag();
function decrypt(t){const decipher=crypto.createDecipheriv('aes-256-gcm',key,iv);
 decipher.setAuthTag(t);return Buffer.concat([decipher.update(encrypted),decipher.final()]);}
assert.deepEqual(decrypt(tag),raw);
const bad=Buffer.from(tag);bad[0]^=1;assert.throws(()=>decrypt(bad));
console.log(JSON.stringify({dump:raw.toString('base64'),encrypted_bytes:encrypted.length}));
"""
    result = subprocess.check_output(
        [
            "docker",
            "run",
            "--rm",
            "-i",
            "--network",
            "none",
            "echosim-m0-node-test:local",
            "node",
            "-e",
            program,
        ],
        cwd=root,
        input=json.dumps({"key": key, "dump": base64.b64encode(data).decode()}).encode(),
    )
    decrypted = json.loads(result)
    pg("createdb", "-U", "echosim_test", "echosim_restored")
    pg(
        "pg_restore",
        "-U",
        "echosim_test",
        "-d",
        "echosim_restored",
        "--exit-on-error",
        input=base64.b64decode(decrypted["dump"]),
    )
    value = (
        pg(
            "psql",
            "-U",
            "echosim_test",
            "-d",
            "echosim_restored",
            "-At",
            "-c",
            "SELECT note FROM drill_records WHERE id=1",
        )
        .decode()
        .strip()
    )
    assert value == "synthetic-only"
    pg("dropdb", "-U", "echosim_test", "echosim_restored")
    pg(
        "psql",
        "-U",
        "echosim_test",
        "-d",
        "echosim_test",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        "DROP TABLE drill_records",
    )
    (artifacts / "restore-drill.json").write_text(
        json.dumps(
            {
                "scope": "local synthetic isolated database; NOT production disaster recovery",
                "authenticated_encryption": True,
                "algorithm": "AES-256-GCM (Node built-in crypto)",
                "tamper_rejected": True,
                "restored_value_verified": True,
                "key_persisted": False,
                "seconds": round(time.monotonic() - started, 3),
                "scenario": "SC-GATE-09",
            },
            indent=2,
        )
        + "\n"
    )
