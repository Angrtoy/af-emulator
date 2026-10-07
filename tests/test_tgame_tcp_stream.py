from pathlib import Path
import ast
import heapq
import os
import struct
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "server" / "assaultfire_server_v143b.py"


def load_server_symbols(function_names, constant_names=()):
    """Load pure helpers and module-level constants without boot-time state."""
    source = SERVER_PATH.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(source)
    wanted_funcs = set(function_names)
    wanted_consts = set(constant_names)
    body = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted_funcs:
            body.append(node)
            wanted_funcs.discard(node.name)
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in wanted_consts for t in node.targets
        ):
            body.append(node)
            wanted_consts -= {t.id for t in node.targets if isinstance(t, ast.Name)}
    missing = wanted_funcs | wanted_consts
    if missing:
        raise AssertionError(f"server symbols not found: {sorted(missing)}")
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    ns = {"struct": struct, "os": os, "time": time, "heapq": heapq}
    exec(compile(module, str(SERVER_PATH), "exec"), ns)
    return ns


def load_server_function(name):
    """Load one pure helper (plus the frame-limit constants) from the server."""
    ns = load_server_symbols(
        [name],
        ["TGAME_MAX_HEAD_LEN", "TGAME_MAX_FRAME_LEN"],
    )
    return ns[name]


split_stream = load_server_function("tgame_split_generic_stream")


def make_generic_frame(cmd=0x00, body_len=32, fill=0xA5):
    head_len = 12
    body = bytes([fill]) * body_len
    return (
        b"\x55\x0e"
        + bytes([cmd & 0xFF, 0x04])
        + struct.pack(">I", head_len)
        + struct.pack(">I", body_len)
        + body
    )


def make_synack_frame():
    # Live PH cmd09 shape from the timeout trace:
    # HeadLen=45, BodyLen=16 => 61 total bytes.
    # The 33-byte head extension is [enc_len=32] + 32 encrypted bytes.
    extension = b"\x20" + (b"\xCC" * 32)
    body = b"\xDD" * 16
    head_len = 12 + len(extension)
    return (
        b"\x55\x0e\x09\x04"
        + struct.pack(">I", head_len)
        + struct.pack(">I", len(body))
        + extension
        + body
    )


class TGameTCPStreamTests(unittest.TestCase):
    def test_pre_chgskey_cmd00_and_synack_coalesced(self):
        first = make_generic_frame(cmd=0x00, body_len=32, fill=0x11)
        synack = make_synack_frame()
        frames, tail = split_stream(first + synack)
        self.assertEqual(frames, [first, synack])
        self.assertEqual(tail, b"")

    def test_synack_fragmented_across_recvs(self):
        synack = make_synack_frame()
        frames1, tail1 = split_stream(synack[:17])
        self.assertEqual(frames1, [])
        self.assertEqual(tail1, synack[:17])

        frames2, tail2 = split_stream(tail1 + synack[17:])
        self.assertEqual(frames2, [synack])
        self.assertEqual(tail2, b"")

    def test_synack_and_next_frame_coalesced(self):
        synack = make_synack_frame()
        next_frame = make_generic_frame(cmd=0x00, body_len=48, fill=0x22)
        frames, tail = split_stream(synack + next_frame)
        self.assertEqual(frames, [synack, next_frame])
        self.assertEqual(tail, b"")

    def test_standalone_synack_is_one_complete_frame(self):
        synack = make_synack_frame()
        frames, tail = split_stream(synack)
        self.assertEqual(frames, [synack])
        self.assertEqual(tail, b"")

    def test_server_enables_stream_framing_before_chgskey(self):
        source = SERVER_PATH.read_text(encoding="utf-8", errors="replace")
        self.assertIn(
            'tgame_stream_mode = bool(\n'
            '            role_state.get("tgame") and tgame_mode4_key is not None\n'
            '        )',
            source,
        )
        self.assertIn("tgame_chgskey_complete = True", source)
        self.assertNotIn("post_chgskey_stream_mode = False", source)
        self.assertNotIn("post_chgskey_frame_queue.clear()", source)


    def test_oversized_frame_is_rejected_so_tail_stays_bounded(self):
        ns = load_server_symbols(
            ["tgame_split_generic_stream"],
            ["TGAME_MAX_HEAD_LEN", "TGAME_MAX_FRAME_LEN"],
        )
        limit = ns["TGAME_MAX_FRAME_LEN"]
        header = (
            b"\x55\x0e\x00\x04"
            + struct.pack(">I", 12)
            + struct.pack(">I", limit)  # head 12 + body limit > limit
        )
        with self.assertRaises(ValueError):
            ns["tgame_split_generic_stream"](header)

    def test_frame_at_limit_is_buffered_not_rejected(self):
        ns = load_server_symbols(
            ["tgame_split_generic_stream"],
            ["TGAME_MAX_HEAD_LEN", "TGAME_MAX_FRAME_LEN"],
        )
        limit = ns["TGAME_MAX_FRAME_LEN"]
        header = (
            b"\x55\x0e\x00\x04"
            + struct.pack(">I", 12)
            + struct.pack(">I", limit - 12)
        )
        frames, tail = ns["tgame_split_generic_stream"](header)
        self.assertEqual(frames, [])
        self.assertEqual(tail, header)

    def test_oversized_head_len_is_rejected(self):
        ns = load_server_symbols(
            ["tgame_split_generic_stream"],
            ["TGAME_MAX_HEAD_LEN", "TGAME_MAX_FRAME_LEN"],
        )
        header = (
            b"\x55\x0e\x00\x04"
            + struct.pack(">I", ns["TGAME_MAX_HEAD_LEN"] + 1)
            + struct.pack(">I", 0)
        )
        with self.assertRaises(ValueError):
            ns["tgame_split_generic_stream"](header)


class UDPPeerStatePruneTests(unittest.TestCase):
    def setUp(self):
        self.ns = load_server_symbols(
            ["_env_positive_int", "_prune_udp_peer_state"],
            ["_UDP_PEER_TTL_SECONDS", "_UDP_PEER_MAX_ENTRIES"],
        )
        self.prune = self.ns["_prune_udp_peer_state"]

    def test_idle_peers_expire_and_active_peers_stay(self):
        peers = {
            ("1.1.1.1", 1): {"last_seen": 100.0},
            ("2.2.2.2", 2): {"last_seen": 990.0},
        }
        removed = self.prune(peers, now=1000.0, ttl=600, max_entries=10)
        self.assertEqual(removed, 1)
        self.assertEqual(list(peers), [("2.2.2.2", 2)])

    def test_entry_without_last_seen_counts_as_expired(self):
        peers = {("3.3.3.3", 3): {}}
        self.assertEqual(self.prune(peers, now=1.0, ttl=600, max_entries=10), 1)
        self.assertEqual(peers, {})

    def test_cap_evicts_least_recently_seen_first(self):
        peers = {("h", i): {"last_seen": 1000.0 + i} for i in range(10)}
        removed = self.prune(peers, now=1010.0, ttl=600, max_entries=4)
        self.assertEqual(removed, 6)
        self.assertEqual(sorted(a[1] for a in peers), [6, 7, 8, 9])

    def test_flood_stays_under_cap_with_amortized_pruning(self):
        peers = {}
        prunes = 0
        for i in range(50_000):
            peers[("10.0.%d.%d" % (i // 250, i % 250), i)] = {"last_seen": float(i)}
            if len(peers) > 4096:
                self.prune(peers, now=float(i), ttl=10**9, max_entries=4096)
                prunes += 1
        self.assertLessEqual(len(peers), 4096)
        # Low-water eviction: roughly one prune per ~410 new peers, not one per packet.
        self.assertLess(prunes, 200)

    def test_server_prunes_and_drops_undecodable_first_packet(self):
        source = SERVER_PATH.read_text(encoding="utf-8", errors="replace")
        self.assertIn("_prune_udp_peer_state(peer_state, recv_now)", source)
        self.assertIn('st["last_seen"] = recv_now', source)
        self.assertIn("peer_state.pop(addr, None)", source)


if __name__ == "__main__":
    unittest.main()
