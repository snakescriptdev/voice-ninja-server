"""
Web Agent router (public)

Serves the standalone, full-page "Web Agent" HTML page for a WebAgentPageModel
record. The page renders just a big click-to-talk mic control (positioned per
`agent_position`, colored per `bg_color`) and connects directly to the
underlying Widget's existing, unmodified `/api/v2/widget/ws/{public_id}`
websocket route to place the actual call — no server-side call logic is
duplicated here.
"""

import html
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi_sqlalchemy import db

from app_v2.databases.models import WebAgentPageModel
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)

router = APIRouter(prefix="/api/v2/web-agents", tags=["web-agent"])

_SAFE_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")
_DEFAULT_BG_COLOR = "#0B0B0F"
_DEFAULT_ACCENT_COLOR = "#562C7C"

_JUSTIFY_BY_POSITION = {
    "left": "flex-start",
    "center": "center",
    "right": "flex-end",
}


def _safe_hex_color(color: str, default: str) -> str:
    return color if _SAFE_COLOR_RE.match(color or "") else default


def _safe_bg_color(bg_color: str) -> str:
    return _safe_hex_color(bg_color, _DEFAULT_BG_COLOR)


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """3- or 6-digit hex (leading '#') -> (r, g, b). Caller must have already
    validated the string via _safe_hex_color."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _darken(hex_color: str, factor: float = 0.6) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    return f"#{int(r * factor):02x}{int(g * factor):02x}{int(b * factor):02x}"


def _rgba(hex_color: str, alpha: float) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    return f"rgba({r},{g},{b},{alpha})"


def _build_web_agent_page_html(
    request: Request,
    web_agent_name: str,
    bg_color: str,
    agent_position: str,
    widget_public_id: str,
    primary_color: str,
    show_branding: bool,
) -> str:
    justify_content = _JUSTIFY_BY_POSITION.get(agent_position, "center")
    safe_bg_color = _safe_bg_color(bg_color)
    safe_name = html.escape(web_agent_name)
    # Accent color comes from the linked widget so the hosted full-page call
    # experience visually matches the embeddable widget for the same agent,
    # instead of every web agent page defaulting to the same hardcoded brand
    # gradient regardless of which widget/agent it's for.
    safe_accent = _safe_hex_color(primary_color, _DEFAULT_ACCENT_COLOR)
    accent_dark = _darken(safe_accent)
    branding_html = '<div id="wa-branding">Powered by Voice Ninja</div>' if show_branding else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{safe_name}</title>
  <style>
    html, body {{ margin: 0; padding: 0; height: 100%; }}
    body {{
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: {justify_content};
      background: {safe_bg_color};
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif;
    }}
    .wa-wrap {{ display: flex; flex-direction: column; align-items: center; gap: 16px; padding: 0 32px; }}
    #wa-mic-btn {{
      width: 96px; height: 96px; border-radius: 50%; border: none; cursor: pointer;
      background: linear-gradient(145deg, {safe_accent}, {accent_dark});
      display: flex; align-items: center; justify-content: center;
      box-shadow: 0 8px 32px {_rgba(safe_accent, 0.35)};
      transition: box-shadow 0.3s ease, transform 0.2s ease;
    }}
    #wa-mic-btn:hover {{ transform: scale(1.04); }}
    #wa-mic-btn.wa-connecting {{ animation: wa-pulse 1.6s ease-in-out infinite; }}
    #wa-mic-btn.wa-active {{ box-shadow: 0 0 0 10px {_rgba(safe_accent, 0.18)}, 0 8px 32px {_rgba(safe_accent, 0.35)}; }}
    @keyframes wa-pulse {{ 0%, 100% {{ box-shadow: 0 0 0 0 {_rgba(safe_accent, 0.35)}; }} 50% {{ box-shadow: 0 0 0 16px {_rgba(safe_accent, 0.05)}; }} }}
    #wa-mic-btn svg {{ width: 36px; height: 36px; fill: #fff; }}
    #wa-status {{ color: #fff; opacity: 0.85; font-size: 14px; min-height: 20px; }}
    #wa-branding {{ color: #fff; opacity: 0.4; font-size: 10px; text-align: center; margin-top: -8px; }}
  </style>
</head>
<body>
  <div class="wa-wrap">
    <button id="wa-mic-btn" title="Click to talk">
      <svg viewBox="0 0 24 24"><path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2z"/></svg>
    </button>
    <div id="wa-status"></div>
    {branding_html}
  </div>

  <script>
  (function() {{
    var widgetPublicId = '{widget_public_id}';
    var wsScheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    var wsUrl = wsScheme + '//' + window.location.host + '/api/v2/widget/ws/' + widgetPublicId + '?source=web_agent';

    var btn = document.getElementById('wa-mic-btn');
    var statusEl = document.getElementById('wa-status');

    var SAMPLE_RATE = 16000;
    var connected = false;
    var connecting = false;
    var ws = null;
    var audioContext = null;
    var mic = null;
    var processor = null;
    var audioReady = false;
    var audioQueue = [];
    var isPlaying = false;
    var currentSrc = null;
    var stopped = false;

    function setStatus(text) {{ statusEl.textContent = text || ''; }}
    function setState(state) {{
      btn.classList.remove('wa-active', 'wa-connecting');
      if (state === 'connecting') btn.classList.add('wa-connecting');
      else if (state === 'active') btn.classList.add('wa-active');
    }}

    function stopPlayback() {{
      audioQueue = [];
      if (currentSrc) {{ try {{ currentSrc.stop(); }} catch (e) {{}} currentSrc = null; }}
      isPlaying = false;
    }}

    function releaseMic() {{
      stopped = true;
      if (processor) {{ try {{ processor.disconnect(); }} catch (e) {{}} processor = null; }}
      if (mic) {{ mic.getTracks().forEach(function(t) {{ t.stop(); }}); mic = null; }}
    }}

    function playNext() {{
      if (!audioQueue.length) {{ isPlaying = false; currentSrc = null; return; }}
      isPlaying = true;
      var int16 = new Int16Array((audioQueue.shift()).buffer);
      var f32 = new Float32Array(int16.length);
      for (var i = 0; i < int16.length; i++) f32[i] = int16[i] / 32768;
      var ab = audioContext.createBuffer(1, f32.length, SAMPLE_RATE);
      ab.getChannelData(0).set(f32);
      var src = audioContext.createBufferSource();
      src.buffer = ab;
      src.connect(audioContext.destination);
      src.onended = function() {{ currentSrc = null; setTimeout(playNext, 0); }};
      currentSrc = src;
      src.start();
    }}

    function queuePlay(buf) {{
      audioQueue.push(buf);
      if (!isPlaying) playNext();
    }}

    function startStreaming() {{
      if (!mic || !audioContext || !audioReady) return;
      var src = audioContext.createMediaStreamSource(mic);
      processor = audioContext.createScriptProcessor(4096, 1, 1);
      processor.onaudioprocess = function(ev) {{
        if (!ws || ws.readyState !== 1) return;
        var input = ev.inputBuffer.getChannelData(0);
        var pcm = new Int16Array(input.length);
        for (var i = 0; i < input.length; i++) pcm[i] = Math.max(-32768, Math.min(32767, input[i] * 32767));
        ws.send(JSON.stringify({{ type: 'user_audio_chunk', data_b64: btoa(String.fromCharCode.apply(null, new Uint8Array(pcm.buffer))) }}));
      }};
      src.connect(processor);
      processor.connect(audioContext.destination);
    }}

    function unlockAndStream() {{
      audioContext = new (window.AudioContext || window.webkitAudioContext)({{ sampleRate: SAMPLE_RATE }});
      audioContext.resume().then(function() {{
        navigator.mediaDevices.getUserMedia({{ audio: {{ sampleRate: SAMPLE_RATE, channelCount: 1 }} }})
          .then(function(stream) {{
            if (stopped) {{ stream.getTracks().forEach(function(t) {{ t.stop(); }}); return; }}
            mic = stream;
            startStreaming();
          }})
          .catch(function() {{ setStatus('Microphone access denied'); }});
      }});
    }}

    function connect() {{
      return new Promise(function(resolve, reject) {{
        ws = new WebSocket(wsUrl);
        ws.onopen = function() {{
          ws.send(JSON.stringify({{ type: 'conversation_init', language: 'en', model: 'eleven_turbo_v2' }}));
          resolve();
        }};
        ws.onmessage = function(ev) {{
          try {{
            var msg = JSON.parse(ev.data);
            if (msg.type === 'audio_interface_ready') {{ audioReady = true; if (audioContext) startStreaming(); }}
            if (msg.type === 'audio_chunk' && msg.data_b64) {{
              queuePlay(Uint8Array.from(atob(msg.data_b64), function(c) {{ return c.charCodeAt(0); }}));
            }}
            if (msg.type === 'interruption') {{ stopPlayback(); }}
            if (msg.type === 'call_ended') {{
              stopPlayback(); releaseMic();
              setStatus(msg.message || 'Call ended');
            }}
            if (msg.type === 'error') {{
              connected = false; connecting = false;
              stopPlayback(); releaseMic();
              setState('idle');
              setStatus(msg.message || 'Connection error');
            }}
          }} catch (e) {{}}
        }};
        ws.onclose = function() {{
          connected = false;
          stopPlayback(); releaseMic();
          connecting = false;
          setState('idle');
        }};
        ws.onerror = function() {{ reject(new Error('WebSocket error')); }};
      }});
    }}

    function endCall() {{
      stopPlayback();
      releaseMic();
      if (ws) ws.close();
      connected = false;
      setState('idle');
      setStatus('');
    }}

    function startCall() {{
      stopped = false;
      connecting = true;
      setState('connecting');
      setStatus('Connecting...');
      connect()
        .then(function() {{
          connected = true; connecting = false;
          setState('active');
          setStatus('Listening...');
          unlockAndStream();
        }})
        .catch(function() {{ connecting = false; setState('idle'); setStatus('Connection failed'); }});
    }}

    btn.addEventListener('click', function() {{
      if (connecting) return;
      if (connected) {{ endCall(); }} else {{ startCall(); }}
    }});
  }})();
  </script>
</body>
</html>"""


@router.get("/{public_id}/web-agent", response_class=HTMLResponse, summary="Public Web Agent page")
async def web_agent_page(request: Request, public_id: str):
    with db():
        web_agent = db.session.query(WebAgentPageModel).filter(WebAgentPageModel.public_id == public_id).first()
        if not web_agent:
            raise HTTPException(status_code=404, detail="Web agent not found")
        if not web_agent.is_enabled:
            return HTMLResponse("<html><body><h1>This web agent is disabled</h1></body></html>", status_code=403)

        widget = web_agent.widget
        if not widget or not widget.is_enabled:
            return HTMLResponse("<html><body><h1>The linked widget is disabled</h1></body></html>", status_code=403)
        if not widget.agent or not widget.agent.is_enabled:
            return HTMLResponse("<html><body><h1>Voice Agent is disabled</h1></body></html>", status_code=403)
        if not widget.agent.elevenlabs_agent_id:
            return HTMLResponse("<html><body><h1>Voice Agent is not configured</h1></body></html>", status_code=403)

        html_page = _build_web_agent_page_html(
            request,
            web_agent.web_agent_name,
            web_agent.bg_color,
            web_agent.agent_position,
            widget.public_id,
            widget.primary_color,
            widget.show_branding,
        )

    return HTMLResponse(html_page)
