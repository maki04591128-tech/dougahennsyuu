"""テスト用の偽推論サーバー（httpx.MockTransport）。

2026-11-23 の実機調査（Ollama 0.40.16 + 3モデル）で観測した挙動を再現する:
- qwen3.5-9b-local / gemma4-e4b-local: tool_calls をネイティブ返却
- qwen3-swallow-8b-local: tool_calls を返さず本文にJSONを書く
- gemma4-e4b-local: JSON Schema の形式は守るが duration_sec が値域外
- いずれも mmproj 無しのため画像入力は 400
"""

import json
from typing import Any

import httpx

OLLAMA = "127.0.0.1:11434"
LMSTUDIO = "127.0.0.1:1234"

OLLAMA_MODELS: dict[str, dict[str, Any]] = {
    "qwen3.5-9b-local:latest": {
        "family": "qwen35",
        "params": "9.0B",
        "quant": "Q4_K_M",
        "size": 5_290_000_000,
        "ctx_key": "qwen35.context_length",
        "ctx": 262144,
        "tools": "native",
        "json": {"title": "未来都市", "duration_sec": 845, "tags": ["SF"]},
    },
    "gemma4-e4b-local:latest": {
        "family": "gemma4",
        "params": "7.5B",
        "quant": "Q4_0",
        "size": 4_280_000_000,
        "ctx_key": "gemma4.context_length",
        "ctx": 131072,
        "tools": "native",
        "json": {"title": "AI Vlog", "duration_sec": 1245000000000000, "tags": ["AI"]},
    },
    "qwen3-swallow-8b-local:latest": {
        "family": "qwen3",
        "params": "8.2B",
        "quant": "Q4_K_M",
        "size": 4_680_000_000,
        "ctx_key": "qwen3.context_length",
        "ctx": 40960,
        "tools": "content",
        "json": {"title": "攻略ガイド", "duration_sec": 423, "tags": ["ゲーム"]},
    },
}


class FakeServers:
    def __init__(self) -> None:
        self.ollama_up = True
        self.lmstudio_up = False
        self.lmstudio_rest_v1 = True
        self.ollama_loaded: set[str] = set()
        self.lm_loaded: dict[str, str] = {"qwen/qwen3-8b": "inst-1"}
        self.requests: list[tuple[str, str, Any]] = []
        # /api/chat の応答を差し替える（body -> content 文字列 or dict）。None で既定応答
        self.chat_responder: Any = None
        self.chat_bodies: list[dict[str, Any]] = []
        self.nemo = FakeNemo()
        from fake_assets import FakeAceStep, FakeAssetWeb

        self.web = FakeAssetWeb()
        self.acestep = FakeAceStep()

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        host = f"{request.url.host}:{request.url.port}"
        if host == NEMO:
            return self.nemo.handle(request)
        if self.web.handles(request.url.host):
            return self.web.handle(request)
        if host == "127.0.0.1:8001":
            return self.acestep.handle(request)
        body = json.loads(request.content) if request.content else None
        self.requests.append((host, request.url.path, body))
        if host == OLLAMA and self.ollama_up:
            return self._ollama(request, body)
        if host == LMSTUDIO and self.lmstudio_up:
            return self._lmstudio(request, body)
        raise httpx.ConnectError("connection refused", request=request)

    # ---------------- Ollama ----------------
    def _ollama(self, req: httpx.Request, body: Any) -> httpx.Response:
        path = req.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.40.16"})
        if path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": n,
                            "model": n,
                            "size": m["size"],
                            "details": {
                                "format": "gguf",
                                "family": m["family"],
                                "parameter_size": m["params"],
                                "quantization_level": m["quant"],
                            },
                        }
                        for n, m in OLLAMA_MODELS.items()
                    ]
                },
            )
        if path == "/api/show":
            m = OLLAMA_MODELS.get(body["model"])
            if m is None:
                return httpx.Response(404, json={"error": f"model '{body['model']}' not found"})
            return httpx.Response(
                200,
                json={
                    "capabilities": ["completion", "tools", "thinking"],
                    "model_info": {m["ctx_key"]: m["ctx"]},
                },
            )
        if path == "/api/ps":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": n, "model": n, "size_vram": 5_000_000_000}
                        for n in self.ollama_loaded
                    ]
                },
            )
        if path == "/api/generate":
            if body.get("keep_alive") == 0:
                self.ollama_loaded.discard(body["model"])
            else:
                self.ollama_loaded.add(body["model"])
            return httpx.Response(200, json={"model": body["model"], "done": True})
        if path == "/api/chat":
            return self._ollama_chat(body)
        return httpx.Response(404, text="404 page not found")

    def _ollama_chat(self, body: dict[str, Any]) -> httpx.Response:
        name = body["model"]
        m = OLLAMA_MODELS.get(name)
        if m is None:
            return httpx.Response(404, json={"error": f"model '{name}' not found"})
        if any(msg.get("images") for msg in body["messages"]):
            return httpx.Response(
                400,
                json={
                    "error": "Multimodal data provided, "
                    "but model does not support multimodal requests."
                },
            )
        self.ollama_loaded.add(name)
        self.chat_bodies.append(body)
        msg: dict[str, Any] = {"role": "assistant", "content": ""}
        custom = self.chat_responder(body) if self.chat_responder else None
        if custom is not None:
            msg["content"] = (
                custom if isinstance(custom, str) else json.dumps(custom, ensure_ascii=False)
            )
        elif body.get("tools"):
            tool = body["tools"][0]["function"]["name"]
            if m["tools"] == "native":
                msg["tool_calls"] = [
                    {
                        "id": "call_1",
                        "function": {"index": 0, "name": tool, "arguments": {"city": "東京"}},
                    }
                ]
            else:
                msg["content"] = json.dumps(
                    {"name": tool, "arguments": {"city": "東京"}}, ensure_ascii=False
                )
        elif body.get("format"):
            msg["content"] = json.dumps(m["json"], ensure_ascii=False)
        else:
            msg["content"] = "動画編集を自動化すると作業時間を短縮できます。品質も安定します。"
        if body.get("stream"):
            lines = [
                json.dumps(
                    {"message": {"content": "こんにちは"}, "done": False}, ensure_ascii=False
                ),
                json.dumps({"message": {"content": "！"}, "done": False}, ensure_ascii=False),
                json.dumps({"message": {"content": ""}, "done": True}),
            ]
            return httpx.Response(200, content=("\n".join(lines) + "\n").encode())
        return httpx.Response(
            200,
            json={
                "model": name,
                "message": msg,
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 20,
                "eval_count": 60,
                "eval_duration": 1_200_000_000,
                "total_duration": 2_000_000_000,
            },
        )

    # ---------------- LM Studio ----------------
    def _instances(self, key: str) -> list[dict[str, Any]]:
        inst = self.lm_loaded.get(key)
        return [{"id": inst, "config": {"context_length": 4096}}] if inst else []

    def _lmstudio(self, req: httpx.Request, body: Any) -> httpx.Response:
        path = req.url.path
        models = [
            {
                "type": "llm",
                "key": "qwen/qwen3-8b",
                "display_name": "Qwen3 8B",
                "architecture": "qwen3",
                "quantization": {"name": "Q4_K_M", "bits_per_weight": 4},
                "size_bytes": 5_000_000_000,
                "params_string": "8B",
                "loaded_instances": self._instances("qwen/qwen3-8b"),
                "max_context_length": 40960,
                "format": "gguf",
                "capabilities": {"vision": False, "trained_for_tool_use": True},
            },
            {
                "type": "llm",
                "key": "google/gemma-4-e4b",
                "display_name": "Gemma 4 E4B",
                "quantization": {"name": "Q4_K_M"},
                "loaded_instances": self._instances("google/gemma-4-e4b"),
                "max_context_length": 131072,
                "format": "gguf",
                "capabilities": {"vision": True, "trained_for_tool_use": True},
            },
            {"type": "embedding", "key": "text-embedding-nomic", "loaded_instances": []},
        ]
        if path == "/api/v1/models" and self.lmstudio_rest_v1:
            return httpx.Response(200, json={"models": models})
        if path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": m["key"]} for m in models]})
        if path == "/api/v1/models/load":
            self.lm_loaded[body["model"]] = f"inst-{len(self.lm_loaded) + 1}"
            return httpx.Response(200, json={"type": "llm", "instance_id": "x", "status": "loaded"})
        if path == "/api/v1/models/unload":
            self.lm_loaded = {k: v for k, v in self.lm_loaded.items() if v != body["instance_id"]}
            return httpx.Response(200, json={"instance_id": body["instance_id"]})
        if path == "/v1/chat/completions":
            if body.get("stream"):
                chunks = [
                    {"choices": [{"delta": {"content": "やあ"}, "finish_reason": None}]},
                    {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                ]
                sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
                return httpx.Response(200, content=sse.encode())
            msg: dict[str, Any] = {"role": "assistant", "content": "こんにちは"}
            if body.get("tools"):
                msg = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {
                                "name": body["tools"][0]["function"]["name"],
                                "arguments": '{"city": "東京"}',
                            },
                        }
                    ],
                }
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": msg, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 30},
                    "stats": {"tokens_per_second": 42.04},
                },
            )
        return httpx.Response(404, json={"error": "Unexpected endpoint"})


class FakeProtector:
    """DPAPIの代替（テスト用）。可逆だが平文ではない。"""

    def protect(self, data: bytes) -> bytes:
        return b"ENC:" + bytes(b ^ 0x5A for b in data)

    def unprotect(self, blob: bytes) -> bytes:
        assert blob.startswith(b"ENC:")
        return bytes(b ^ 0x5A for b in blob[4:])


# ---------------- VOICEVOX Nemo (port 50121) ----------------
NEMO = "127.0.0.1:50121"
_NEMO_SPEAKERS = json.loads(
    (__import__("pathlib").Path(__file__).parent / "data" / "nemo_speakers.json").read_text(
        encoding="utf-8"
    )
)
NEMO_FEATURES = {
    k: {"type": "bool", "value": v, "name": k}
    for k, v in {
        "adjust_mora_pitch": True,
        "adjust_phoneme_length": True,
        "adjust_speed_scale": True,
        "adjust_pitch_scale": True,
        "adjust_intonation_scale": True,
        "adjust_volume_scale": True,
        "adjust_pause_length": True,
        "interrogative_upspeak": True,
        "synthesis_morphing": True,
        "sing": False,
        "manage_library": False,
        "streaming_synthesis": True,
    }.items()
}


def _wav(seconds: float, rate: int = 24000) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        # 実際の音声に近いよう、無音ではなく -20 dBFS 程度の正弦波（品質検査で測れる）
        import math
        import struct

        n = round(seconds * rate)
        amp = 3277
        w.writeframes(
            b"".join(
                struct.pack("<h", int(amp * math.sin(2 * math.pi * 220 * i / rate)))
                for i in range(n)
            )
        )
    return buf.getvalue()


class FakeNemo:
    """実機 Nemo 0.26.0 の API 形状を模した偽エンジン。1文字=1モーラの簡易解析。"""

    def __init__(self) -> None:
        self.up = True
        self.features = dict(NEMO_FEATURES)
        self.user_dict: dict[str, dict] = {}
        self.synth_queries: list[dict] = []

    def phrases(self, text: str) -> list[dict]:
        out = []
        for part in [p for p in text.replace("。", "、").split("、") if p]:
            moras = [
                {
                    "text": ch,
                    "consonant": "k",
                    "consonant_length": 0.05,
                    "vowel": "a",
                    "vowel_length": 0.1,
                    "pitch": 5.5,
                }
                for ch in part
            ]
            out.append(
                {
                    "moras": moras,
                    "accent": 1,
                    "pause_mora": {"text": "、", "vowel": "pau", "vowel_length": 0.3, "pitch": 0.0},
                    "is_interrogative": False,
                }
            )
        if out:
            out[-1]["pause_mora"] = None
        return out

    def handle(self, req: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("refused", request=req)
        p, q = req.url.path, dict(req.url.params)
        body = json.loads(req.content) if req.content else None
        valid_styles = {s["id"] for sp in _NEMO_SPEAKERS for s in sp["styles"]}
        if "speaker" in q and int(q["speaker"]) not in valid_styles:
            return httpx.Response(422, json={"detail": "該当する話者が見つかりません"})
        if p == "/version":
            return httpx.Response(200, json="0.26.0")
        if p == "/engine_manifest":
            return httpx.Response(
                200,
                json={
                    "name": "VOICEVOX Nemo Engine",
                    "version": "0.26.0",
                    "port": 50121,
                    "default_sampling_rate": 24000,
                    "frame_rate": 93.75,
                    "supported_features": self.features,
                },
            )
        if p == "/speakers":
            return httpx.Response(200, json=_NEMO_SPEAKERS)
        if p == "/supported_devices":
            return httpx.Response(200, json={"cpu": True, "cuda": False, "dml": False})
        if p in ("/audio_query", "/accent_phrases"):
            if "@" in q["text"]:
                return httpx.Response(400, json={"detail": "parse error"})
            phrases = self.phrases(q["text"])
            if p == "/accent_phrases":
                return httpx.Response(200, json=phrases)
            return httpx.Response(
                200,
                json={
                    "accent_phrases": phrases,
                    "speedScale": 1.0,
                    "pitchScale": 0.0,
                    "intonationScale": 1.0,
                    "volumeScale": 1.0,
                    "prePhonemeLength": 0.1,
                    "postPhonemeLength": 0.1,
                    "pauseLength": None,
                    "pauseLengthScale": 1.0,
                    "outputSamplingRate": 24000,
                    "outputStereo": False,
                    "kana": q["text"],
                },
            )
        if p in ("/mora_data", "/mora_pitch", "/mora_length"):
            for ap in body:
                for i, m in enumerate(ap["moras"]):
                    if p != "/mora_length":
                        m["pitch"] = 6.0 if i < ap["accent"] else 5.0
                    if p != "/mora_pitch":
                        m["vowel_length"] = 0.12
            return httpx.Response(200, json=body)
        if p == "/synthesis":
            from video_automation.schemas.voice import AudioQuery
            from video_automation.services.voice.timing import estimated_duration

            self.synth_queries.append(body)
            return httpx.Response(
                200,
                content=_wav(estimated_duration(AudioQuery.model_validate(body))),
                headers={"content-type": "audio/wav"},
            )
        if p == "/user_dict":
            return httpx.Response(200, json=self.user_dict)
        if p == "/user_dict_word" and req.method == "POST":
            wid = f"w-{len(self.user_dict) + 1}"
            self.user_dict[wid] = dict(q)
            return httpx.Response(200, json=wid)
        if p.startswith("/user_dict_word/"):
            wid = p.rsplit("/", 1)[1]
            if wid not in self.user_dict:
                return httpx.Response(422, json={"detail": "not found"})
            if req.method == "DELETE":
                del self.user_dict[wid]
            else:
                self.user_dict[wid] = dict(q)
            return httpx.Response(204)
        return httpx.Response(404, json={"detail": "Not Found"})


def fake_blender(tmp_path) -> str:
    """偽の blender 実行ファイル。同梱スクリプト（title_card / infographic）の呼び出しを
    calls.jsonl に記録し、中央に白い四角のある透過 PNG 連番と結果JSONを書く。"""
    import shutil
    import stat
    import sys
    import textwrap

    ff = shutil.which("ffmpeg")
    log = tmp_path / "calls.jsonl"
    script = tmp_path / "fake_blender.py"
    script.write_text(
        textwrap.dedent(f"""\
        import json, subprocess, sys
        from pathlib import Path
        if "--version" in sys.argv:
            print("Blender 5.3.0"); sys.exit(0)
        a = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
        name = Path(sys.argv[sys.argv.index("--python") + 1]).stem
        assert name in ("title_card", "infographic", "rich_direction", "motion_bg"), name
        with open({str(log)!r}, "a", encoding="utf-8") as f:
            f.write(json.dumps({{"script": name, **a}}, ensure_ascii=False) + "\\n")
        if name == "rich_direction":
            # 冒頭の演出の分（intro_frames）だけ黒を足し、音を遅らせて書き出す
            intro = a["params"].get("intro_frames", 0) if a["parts"].get("intro") and (
                a.get("catch") or a.get("title")) else 0
            fps = a["fps_num"] / a["fps_den"]
            d = intro / fps
            subprocess.run([{ff!r}, "-v", "error", "-y", "-i", a["clean_path"],
                "-vf", f"tpad=start_duration={{d}}:color=black",
                "-af", f"adelay={{int(d * 1000)}}:all=1",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", a["out_path"]],
                check=True)
            used = {{k: 1 for k, v in a["parts"].items() if v}}
            Path(a["result_path"]).write_text(json.dumps({{"intro_frames": intro,
                "used": used, "parts": {{k: {{"ok": True}} for k, v in a["parts"].items() if v}},
                "render_sec": 0.1}}))
            sys.exit(0)
        out = Path(a["output_dir"])
        w, h = a["width"], a["height"]
        bw, bh = w // 2, h // 5
        bx, by = (w - bw) // 2, (h - bh) // 2
        subprocess.run([{ff!r}, "-v", "error", "-y", "-f", "lavfi", "-i",
            f"color=c=black@0.0:s={{w}}x{{h}}:r={{a['fps']}},format=rgba,"
            f"drawbox=x={{bx}}:y={{by}}:w={{bw}}:h={{bh}}:color=white:t=fill:replace=1",
            "-frames:v", str(a["frames"]), "-pix_fmt", "rgba", str(out / "frame_%04d.png")],
            check=True)
        Path(a["result_path"]).write_text(json.dumps({{"frames": a["frames"], "engine": "FAKE",
            "font_loaded": bool(a.get("font_path")), "text_box": [bx, by, bw, bh]}}))
    """),
        encoding="utf-8",
    )
    if sys.platform == "win32":
        # Windows はシバンで起動できないため、Python を呼ぶ .cmd を実行ファイルにする
        cmd = tmp_path / "blender.cmd"
        cmd.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        return str(cmd)
    exe = tmp_path / "blender"
    exe.write_text(f"#!{sys.executable}\n" + script.read_text(encoding="utf-8"), encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe)


def blender_calls(tmp_path) -> list[dict]:
    p = tmp_path / "calls.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x]
