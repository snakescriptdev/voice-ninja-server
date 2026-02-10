"""
Web Agent router: embed script and WebSocket proxy for voice chat widget.
Uses ElevenLabs Conversational AI behind our API; agents are identified by agent id (integer).
"""

import asyncio
import base64
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import Response, HTMLResponse

from fastapi_sqlalchemy import db
from sqlalchemy.orm import selectinload

from app_v2.databases.models import AgentModel, AgentLanguageBridge
from app_v2.core.logger import setup_logger
from app_v2.core.elevenlabs_config import ELEVENLABS_API_KEY

logger = setup_logger(__name__)

router = APIRouter(
    prefix="/api/v2/web-agent",
    tags=["web-agent"],
)

ACTIVE_SESSIONS: dict = {}

# Voice Ninja logo SVG (from archive/static/Web/images/voice-ninja-logo.svg)
VOICE_NINJA_LOGO_SVG = """<svg width="62" height="21" viewBox="0 0 62 21" fill="none" xmlns="http://www.w3.org/2000/svg">
<path fill-rule="evenodd" clip-rule="evenodd" d="M0 20.7579C0 13.9598 5.51102 8.44873 12.3092 8.44873H48.9212C48.9212 15.2469 43.4102 20.7579 36.612 20.7579H0ZM20.3495 17.188C19.5605 18.7218 16.946 18.9494 14.5099 17.6963C12.0738 16.4432 10.2573 13.9279 11.0463 12.3941C11.8353 10.8602 16.0273 12.7191 18.4634 13.9722C18.556 14.0342 18.646 14.0941 18.7333 14.1523C20.4231 15.2785 21.0997 15.7294 20.3495 17.188ZM34.8439 17.6963C32.4078 18.9494 29.7933 18.7218 29.0043 17.188C28.2541 15.7294 28.9306 15.2785 30.6205 14.1523C30.7078 14.0941 30.7977 14.0342 30.8904 13.9722C33.3265 12.7191 37.5185 10.8602 38.3074 12.3941C39.0964 13.9279 37.28 16.4432 34.8439 17.6963Z" fill="url(#paint0_linear_113_516)"/>
<path d="M49.8682 6.5552C49.8682 3.41757 52.4117 0.874023 55.5493 0.874023H61.5461C61.5461 4.01165 59.0026 6.5552 55.865 6.5552H49.8682Z" fill="url(#paint1_linear_113_516)"/>
<defs>
<linearGradient id="paint0_linear_113_516" x1="0" y1="14.6033" x2="48.9212" y2="14.6033" gradientUnits="userSpaceOnUse"><stop stop-color="#E06943"/><stop offset="0.425" stop-color="#AC1E7A"/><stop offset="0.775" stop-color="#562C7C"/><stop offset="1" stop-color="#34399B"/></linearGradient>
<linearGradient id="paint1_linear_113_516" x1="49.8682" y1="3.71461" x2="61.5461" y2="3.71461" gradientUnits="userSpaceOnUse"><stop stop-color="#E06943"/><stop offset="0.425" stop-color="#AC1E7A"/><stop offset="0.775" stop-color="#562C7C"/><stop offset="1" stop-color="#34399B"/></linearGradient>
</defs>
</svg>"""


# ---------- BrowserAudioInterface: bridge browser WS <-> ElevenLabs ----------


class BrowserAudioInterface:
    """
    Bridges ElevenLabs Conversation audio with the browser WebSocket.
    - output(audio): send PCM chunks to browser as base64 JSON
    - start(input_callback): store callback; browser audio is pushed via push_user_audio
    """

    def __init__(self, websocket: WebSocket, loop: asyncio.AbstractEventLoop, call_id: str):
        self.websocket = websocket
        self.loop = loop
        self.call_id = call_id
        self._input_cb = None
        self._started = False

    def start(self, input_callback):
        self._input_cb = input_callback
        self._started = True
        logger.info("BrowserAudioInterface started for call_id=%s", self.call_id)
        try:
            if self.websocket.client_state.name == "CONNECTED":
                asyncio.run_coroutine_threadsafe(
                    self.websocket.send_json({
                        "type": "audio_interface_ready",
                        "message": "Audio interface is now active",
                        "ts": datetime.utcnow().isoformat(),
                    }),
                    self.loop,
                )
        except Exception as e:
            logger.error("Error sending audio_interface_ready: %s", e)

    def stop(self):
        self._started = False
        logger.info("BrowserAudioInterface stopped for call_id=%s", self.call_id)

    def output(self, audio: bytes):
        try:
            if self.websocket.client_state.name == "CONNECTED":
                asyncio.run_coroutine_threadsafe(
                    self.websocket.send_json({
                        "type": "audio_chunk",
                        "sample_rate": 16000,
                        "channels": 1,
                        "format": "pcm_s16le",
                        "data_b64": base64.b64encode(audio).decode("ascii"),
                        "ts": datetime.utcnow().isoformat(),
                    }),
                    self.loop,
                )
        except Exception as e:
            logger.error("Error sending audio to browser: %s", e)

    def interrupt(self):
        pass

    def push_user_audio(self, audio: bytes):
        if self._input_cb and audio:
            try:
                self._input_cb(audio)
            except Exception as e:
                logger.error("Error delivering browser audio to ElevenLabs: %s", e)


# ---------- Preview page (paste link in browser) ----------


@router.get(
    "/preview/{agent_id}",
    response_class=HTMLResponse,
    summary="Preview page for web agent (open in browser)",
)
async def preview_page(request: Request, agent_id: int):
    """
    Returns an HTML page that loads the voice chat widget. Paste this URL in your browser to try the agent.
    Example: http://localhost:8000/api/v2/web-agent/preview/1
    """
    agent = (
        db.session.query(AgentModel)
        .filter(AgentModel.id == agent_id)
        .first()
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not agent.elevenlabs_agent_id:
        raise HTTPException(status_code=400, detail="Agent has no ElevenLabs configuration")

    base = str(request.base_url).rstrip("/")
    script_url = f"{base}/api/v2/web-agent/embed.js/{agent_id}"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Voice Ninja – Agent {agent_id}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 0; min-height: 100vh; background: #f5f5f5; }}
    .header {{ padding: 16px 24px; background: #1a1a1a; color: #fff; }}
    .header h1 {{ margin: 0; font-size: 1.25rem; }}
    .header p {{ margin: 8px 0 0; font-size: 0.875rem; opacity: 0.9; }}
  </style>
</head>
<body>
  <script src="{script_url}"></script>
</body>
</html>"""
    return HTMLResponse(html)


# ---------- Logo (for widget) ----------


@router.get(
    "/logo.svg",
    response_class=Response,
    summary="Voice Ninja logo for web widget",
)
async def logo_svg():
    return Response(
        VOICE_NINJA_LOGO_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


# ---------- Embed script endpoint ----------


@router.get(
    "/embed.js/{agent_id}",
    response_class=Response,
    summary="Embed script for web agent widget",
)
async def embed_script(agent_id: int):
    """
    Returns JavaScript that injects the voice chat widget and connects to our WebSocket.
    Use as: <script src="https://your-api/api/v2/web-agent/embed.js/42"></script>
    """
    agent = (
        db.session.query(AgentModel)
        .filter(AgentModel.id == agent_id)
        .first()
    )
    if not agent:
        return Response(
            "// Agent not found.",
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache"},
        )
    if not agent.elevenlabs_agent_id:
        return Response(
            "// Agent has no ElevenLabs configuration.",
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    # Build WS URL from request (caller will load script from their page origin; we need our API host)
    # Script will use same host as the script src for WS
    script_content = _get_embed_script_content(agent_id)
    return Response(
        script_content,
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


def _get_embed_script_content(agent_id: int) -> str:
    """Return full embed script JS (widget + WebSocket client). WS URL is derived from script src (API host)."""
    return r"""
(function() {
  var agentId = %d;
  var script = document.currentScript;
  var wsUrl;
  if (script && script.src) {
    var u = new URL(script.src);
    var proto = u.protocol === 'https:' ? 'wss:' : 'ws:';
    var path = u.pathname.replace(/\/embed\.js\/\d+$/, '') + '/ws/' + agentId;
    wsUrl = proto + '//' + u.host + path;
  } else {
    wsUrl = (location.protocol === 'https:' ? 'wss:' : 'ws:') + '//' + location.host + '/api/v2/web-agent/ws/' + agentId;
  }
  var logoUrl = (script && script.src) ? script.src.replace(/\/embed\.js\/\d+$/, '') + '/logo.svg' : '';

  window.voiceNinjaAgentId = agentId;
  window.voiceNinjaWsUrl = wsUrl;

  var vnStyles = '<style id="vn-widget-styles">' +
    '#voice-ninja-widget .vn-card{background:linear-gradient(165deg,#ffffff 0%%,#fafbff 100%%);border-radius:20px;padding:20px;min-width:280px;box-shadow:0 10px 40px rgba(86,44,124,0.12),0 2px 12px rgba(0,0,0,0.06);border:1px solid rgba(224,105,67,0.08);}' +
    '#voice-ninja-widget .vn-root{font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,\'Helvetica Neue\',sans-serif;}' +
    '#vn-indicator-wrap{min-width:44px;height:48px;border-radius:14px;display:flex;align-items:center;justify-content:center;gap:10px;padding:0 12px;background:linear-gradient(145deg,#fef8f6 0%%,#f6f4ff 100%%);border:1px solid rgba(86,44,124,0.06);flex-shrink:0;}' +
    '#vn-indicator-wrap .vn-logo{height:28px;width:auto;object-fit:contain;display:block;}' +
    '#vn-indicator-wrap .vn-voice-bars{display:flex;align-items:flex-end;gap:3px;height:16px;}' +
    '#vn-indicator-wrap .vn-voice-bars span{width:4px;border-radius:2px;background:linear-gradient(180deg,#E06943,#562C7C);height:4px;opacity:0.4;transition:opacity 0.2s ease;}' +
    '#vn-indicator-wrap.vn-speaking .vn-voice-bars span{opacity:0.95;}' +
    '#vn-indicator-wrap.vn-speaking .vn-voice-bars span:nth-child(1){animation:vn-bar 0.55s ease-in-out 0s infinite alternate;}' +
    '#vn-indicator-wrap.vn-speaking .vn-voice-bars span:nth-child(2){animation:vn-bar 0.55s ease-in-out 0.12s infinite alternate;}' +
    '#vn-indicator-wrap.vn-speaking .vn-voice-bars span:nth-child(3){animation:vn-bar 0.55s ease-in-out 0.24s infinite alternate;}' +
    '#vn-indicator-wrap.vn-speaking .vn-voice-bars span:nth-child(4){animation:vn-bar 0.55s ease-in-out 0.36s infinite alternate;}' +
    '@keyframes vn-bar{from{height:4px;}to{height:16px;}}' +
    '#vn-btn{flex:1;background:linear-gradient(135deg,#E06943 0%%,#a81e7a 50%%,#562C7C 100%%);background-size:200%% 100%%;color:#fff;border:none;border-radius:14px;padding:14px 20px;font-weight:600;font-size:15px;letter-spacing:0.02em;cursor:pointer;transition:transform 0.15s ease,box-shadow 0.2s ease,opacity 0.2s ease;}' +
    '#vn-btn:hover{transform:translateY(-1px);box-shadow:0 8px 24px rgba(86,44,124,0.35);}' +
    '#vn-btn:active{transform:translateY(0);}' +
    '#vn-btn.vn-end{background:linear-gradient(135deg,#5a5a5a 0%%,#3d3d3d 100%%);}' +
    '#vn-btn.vn-end:hover{box-shadow:0 6px 20px rgba(0,0,0,0.25);}' +
    '#vn-status{font-size:12px;color:#64748b;letter-spacing:0.02em;margin-top:10px;min-height:18px;}' +
    '</style>';

  function injectWidget() {
    if (document.getElementById('voice-ninja-widget')) return;
    var div = document.createElement('div');
    div.id = 'voice-ninja-widget';
    div.innerHTML = vnStyles +
    '<div class="vn-root" style="position:fixed;bottom:24px;right:24px;z-index:99999;">' +
      '<div class="vn-card">' +
      '<div style="display:flex;align-items:center;gap:14px;">' +
      '<div id="vn-indicator-wrap" title="Voice Ninja">' +
        (logoUrl ? '<img class="vn-logo" src="' + logoUrl + '" alt="Voice Ninja"/>' : '<div class="vn-logo-fallback" style="width:56px;height:28px;border-radius:10px;background:linear-gradient(135deg,#E06943,#562C7C);flex-shrink:0;"></div>') +
        '<div class="vn-voice-bars"><span></span><span></span><span></span><span></span></div>' +
      '</div>' +
      '<button id="vn-btn" type="button">Start voice chat</button>' +
      '</div>' +
      '<div id="vn-status"></div>' +
      '</div></div>';
    document.body.appendChild(div);

    var btn = document.getElementById('vn-btn');
    var statusEl = document.getElementById('vn-status');
    var connected = false;
    var client = null;

    function VoiceNinjaClient() {
      this.ws = null;
      this.audioContext = null;
      this.mic = null;
      this.processor = null;
      this.conversationReady = false;
      this.audioReady = false;
      this.SAMPLE_RATE = 16000;
      this.audioQueue = [];
      this.isPlaying = false;
      this.currentSource = null;
    }

    VoiceNinjaClient.prototype.connect = function() {
      var self = this;
      return new Promise(function(resolve, reject) {
        self.ws = new WebSocket(wsUrl);
        self.ws.onopen = function() {
          self.ws.send(JSON.stringify({ type: 'conversation_init', language: 'en', model: 'eleven_turbo_v2' }));
          resolve();
        };
        self.ws.onmessage = function(ev) {
          try {
            var msg = JSON.parse(ev.data);
            if (msg.type === 'conversation_ready') self.conversationReady = true;
            if (msg.type === 'audio_interface_ready') {
              self.audioReady = true;
              if (self.audioContext) self.startStreaming();
            }
            if (msg.type === 'audio_chunk' && msg.data_b64) {
              var buf = Uint8Array.from(atob(msg.data_b64), function(c) { return c.charCodeAt(0); });
              self.queuePlay(buf);
            }
            if (msg.type === 'error') statusEl.textContent = msg.message || 'Error';
          } catch (e) {}
        };
        self.ws.onclose = function() {
          connected = false;
          self.stopPlayback();
          var w = document.getElementById('vn-indicator-wrap');
          if (w) w.classList.remove('vn-speaking');
          btn.textContent = 'Start voice chat';
          btn.classList.remove('vn-end');
          statusEl.textContent = 'Disconnected';
        };
        self.ws.onerror = function() { reject(new Error('WebSocket error')); };
        setTimeout(function() { if (!self.ws || self.ws.readyState !== 1) reject(new Error('Timeout')); }, 12000);
      });
    };

    VoiceNinjaClient.prototype.unlockAndStream = function() {
      var self = this;
      this.audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: this.SAMPLE_RATE });
      this.audioContext.resume().then(function() {
        navigator.mediaDevices.getUserMedia({ audio: { sampleRate: self.SAMPLE_RATE, channelCount: 1 } }).then(function(stream) {
          self.mic = stream;
          self.startStreaming();
        }).catch(function(e) { statusEl.textContent = 'Microphone access denied'; });
      });
    };

    VoiceNinjaClient.prototype.startStreaming = function() {
      if (!this.mic || !this.audioContext || !this.audioReady) return;
      var self = this;
      var src = this.audioContext.createMediaStreamSource(this.mic);
      this.processor = this.audioContext.createScriptProcessor(4096, 1, 1);
      this.processor.onaudioprocess = function(ev) {
        if (!self.ws || self.ws.readyState !== 1) return;
        var input = ev.inputBuffer.getChannelData(0);
        var pcm = new Int16Array(input.length);
        for (var i = 0; i < input.length; i++) pcm[i] = Math.max(-32768, Math.min(32767, input[i] * 32767));
        var b64 = btoa(String.fromCharCode.apply(null, new Uint8Array(pcm.buffer)));
        self.ws.send(JSON.stringify({ type: 'user_audio_chunk', data_b64: b64 }));
      };
      src.connect(this.processor);
      this.processor.connect(this.audioContext.destination);
    };

    VoiceNinjaClient.prototype.queuePlay = function(buf) {
      this.audioQueue.push(buf);
      if (!this.isPlaying) this.playNext();
    };

    VoiceNinjaClient.prototype.playNext = function() {
      var wrap = document.getElementById('vn-indicator-wrap');
      if (this.audioQueue.length === 0) {
        this.isPlaying = false;
        this.currentSource = null;
        if (wrap) wrap.classList.remove('vn-speaking');
        return;
      }
      this.isPlaying = true;
      if (wrap) wrap.classList.add('vn-speaking');
      var self = this;
      var buf = this.audioQueue.shift();
      var int16 = new Int16Array(buf.buffer || buf);
      var float32 = new Float32Array(int16.length);
      for (var i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768;
      var ab = this.audioContext.createBuffer(1, float32.length, this.SAMPLE_RATE);
      ab.getChannelData(0).set(float32);
      var src = this.audioContext.createBufferSource();
      src.buffer = ab;
      src.connect(this.audioContext.destination);
      src.onended = function() { self.currentSource = null; setTimeout(function() { self.playNext(); }, 0); };
      this.currentSource = src;
      src.start();
    };

    VoiceNinjaClient.prototype.stopPlayback = function() {
      this.audioQueue = [];
      if (this.currentSource) {
        try { this.currentSource.stop(); } catch (e) {}
        this.currentSource = null;
      }
      this.isPlaying = false;
    };

    VoiceNinjaClient.prototype.disconnect = function() {
      this.stopPlayback();
      if (this.processor) try { this.processor.disconnect(); } catch (e) {}
      if (this.mic) this.mic.getTracks().forEach(function(t) { t.stop(); });
      if (this.ws) this.ws.close();
    };

    btn.addEventListener('click', function() {
      if (connected) {
        if (client) client.disconnect();
        var w = document.getElementById('vn-indicator-wrap');
        if (w) w.classList.remove('vn-speaking');
        btn.textContent = 'Start voice chat';
        btn.classList.remove('vn-end');
        statusEl.textContent = 'Disconnected';
        return;
      }
      statusEl.textContent = 'Connecting...';
      client = new VoiceNinjaClient();
      client.connect().then(function() {
        connected = true;
        btn.textContent = 'End call';
        btn.classList.add('vn-end');
        statusEl.textContent = 'Connecting audio...';
        client.unlockAndStream();
      }).catch(function(e) {
        statusEl.textContent = 'Connection failed';
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', injectWidget);
  } else {
    injectWidget();
  }
})();
""" % (agent_id,)


# ---------- WebSocket proxy ----------


@router.websocket("/ws/{agent_id}")
async def web_agent_ws(websocket: WebSocket, agent_id: int):
    await websocket.accept()
    logger.info("Web agent WS connected for agent_id=%s", agent_id)

    # WebSocket has no request-scoped session; use db() context
    with db():
        agent = (
            db.session.query(AgentModel)
            .options(selectinload(AgentModel.agent_languages).selectinload(AgentLanguageBridge.language))
            .filter(AgentModel.id == agent_id)
            .first()
        )
        if not agent or not agent.elevenlabs_agent_id:
            await websocket.send_json({"type": "error", "message": "Agent not found or not configured"})
            await websocket.close(code=1008)
            return
        elevenlabs_agent_id = agent.elevenlabs_agent_id

    # Use elevenlabs_agent_id (and agent_id) after block; no further DB access in this handler
    call_id = f"web_{agent_id}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    loop = asyncio.get_running_loop()
    audio_if = BrowserAudioInterface(websocket, loop, call_id)

    conversation = None
    conversation_ready = False
    selected_language = "en"
    selected_model = "eleven_turbo_v2"

    try:
        from elevenlabs.client import ElevenLabs
        from elevenlabs.conversational_ai.conversation import Conversation, ConversationInitiationData
    except ImportError:
        await websocket.send_json({"type": "error", "message": "ElevenLabs SDK not available"})
        await websocket.close(code=1011)
        return

    api_key = ELEVENLABS_API_KEY or os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        await websocket.send_json({"type": "error", "message": "Server configuration error"})
        await websocket.close(code=1011)
        return

    client = ElevenLabs(api_key=api_key)

    def on_agent_response(text: str):
        try:
            if websocket.client_state.name == "CONNECTED":
                asyncio.run_coroutine_threadsafe(
                    websocket.send_json({"type": "agent_response", "text": text, "ts": datetime.utcnow().isoformat()}),
                    loop,
                )
        except Exception as e:
            logger.error("Error sending agent_response: %s", e)

    def on_user_transcript(text: str):
        try:
            if websocket.client_state.name == "CONNECTED":
                asyncio.run_coroutine_threadsafe(
                    websocket.send_json({"type": "user_transcript", "text": text, "ts": datetime.utcnow().isoformat()}),
                    loop,
                )
        except Exception as e:
            logger.error("Error sending user_transcript: %s", e)

    while True:
        try:
            data = await websocket.receive_json()
        except WebSocketDisconnect:
            break
        except Exception:
            continue

        msg_type = data.get("type")

        if msg_type == "conversation_init" and not conversation_ready:
            selected_language = data.get("language", "en")
            selected_model = data.get("model", "eleven_turbo_v2")
            en_codes = ["en", "en-US", "en-GB"]
            if selected_language in en_codes and selected_model not in ("eleven_turbo_v2", "eleven_flash_v2"):
                selected_model = "eleven_turbo_v2"
            elif selected_language not in en_codes and selected_model not in ("eleven_turbo_v2_5", "eleven_flash_v2_5", "eleven_multilingual_v2"):
                selected_model = "eleven_turbo_v2_5"

            try:
                config = ConversationInitiationData(
                    user_id=f"web_{agent_id}",
                    conversation_config_override={"agent": {"language": selected_language}},
                    extra_body={"model": selected_model},
                    dynamic_variables={"call_id": call_id},
                )
                conversation = Conversation(
                    client,
                    elevenlabs_agent_id,
                    user_id=f"web_{agent_id}",
                    requires_auth=bool(api_key),
                    audio_interface=audio_if,
                    config=config,
                    callback_agent_response=on_agent_response,
                    callback_user_transcript=on_user_transcript,
                )
                await asyncio.to_thread(conversation.start_session)
                await asyncio.sleep(0.5)
                await websocket.send_json({
                    "type": "conversation_ready",
                    "message": "Conversation ready",
                    "ts": datetime.utcnow().isoformat(),
                })
                conversation_ready = True
            except Exception as e:
                logger.exception("ElevenLabs conversation start failed: %s", e)
                await websocket.send_json({"type": "error", "message": str(e)})
                break

        elif msg_type == "user_audio_chunk" and conversation_ready:
            b64 = data.get("data_b64")
            if b64:
                try:
                    audio_bytes = base64.b64decode(b64)
                    if audio_bytes:
                        audio_if.push_user_audio(audio_bytes)
                except Exception as e:
                    logger.debug("Audio decode error: %s", e)
        elif msg_type == "end":
            break

    try:
        if conversation:
            conversation.end_session()
            conversation.wait_for_session_end()
    except Exception:
        pass
    try:
        if websocket.client_state.name != "DISCONNECTED":
            await websocket.close()
    except Exception:
        pass
    logger.info("Web agent WS closed for agent_id=%s", agent_id)
