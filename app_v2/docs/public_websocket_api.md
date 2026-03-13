# Public WebSocket API Documentation

The Voice Ninja Public WebSocket API allows developers to integrate real-time voice AI capabilities into their own applications. This API bridges your application with ElevenLabs Conversational AI through Voice Ninja's orchestration layer.

## Connection Details

- **WebSocket URL**: `ws://<your-server-domain>/api/v2/public/ws/{agent_id}`
- **Protocol**: Standard WebSocket (ws/wss)

## Authentication

Authentication is performed via a "First Message" pattern. Immediately after opening the WebSocket connection, the client must send a JSON authentication message.

### Authentication Message Format

```json
{
  "type": "auth",
  "client_id": "YOUR_CLIENT_ID",
  "client_secret": "YOUR_CLIENT_SECRET"
}
```

> [!IMPORTANT]
> If an authentication message is not received within 5 seconds of connection, the server will close the connection with code `1008`.

## Message Protocol

The API uses both JSON (text) and binary (bytes) messages.

### Client to Server (Upstream)

1. **Authentication**: (Sent once as the first message)
   ```json
   { "type": "auth", "client_id": "...", "client_secret": "..." }
   ```
2. **Audio Data**: Send raw PCM 16k mono audio chunks as binary messages.
3. **JSON Events**: (Optional) For controlling the conversation or sending manual transcripts.
   ```json
   { "type": "ping" }
   ```

### Server to Client (Downstream)

1. **Status Messages**:
   ```json
   {
     "type": "status",
     "message": "Connected and authenticated",
     "ts": "2023-10-27T10:00:00Z"
   }
   ```
2. **Audio Data**: Raw PCM 16k mono audio chunks sent as binary messages.
3. **Transcript Events**:
   ```json
   {
     "type": "user_transcript",
     "text": "Hello, how are you?",
     "ts": "2023-10-27T10:00:05.123Z"
   }
   ```
4. **Agent Response**:
   ```json
   {
     "type": "agent_response",
     "text": "I am doing well, thank you! How can I help you today?",
     "ts": "2023-10-27T10:00:07.456Z"
   }
   ```
5. **Error Messages**:
   ```json
   {
     "type": "error",
     "message": "Insufficient coins for this operation",
     "code": 1008
   }
   ```

## Event Tracking

You can track the following events in the JSON stream:
- `audio_interface_ready`: Indicates that the audio bridge to ElevenLabs is active.
- `user_transcript`: Real-time transcription of the user's speech.
- `agent_response`: Real-time transcription of the agent's spoken response.
- `conversation_initiation_metadata`: Provides the unique `conversation_id`.

## Error Codes

| Code | Meaning | Description |
| :--- | :--- | :--- |
| `1008` | Policy Violation | Authentication failed, invalid agent, or insufficient balance/limits. |
| `1011` | Internal Error | Server-side error or misconfiguration (e.g., missing XI API Key). |
| `4000` | Generic Error | General error message sent in the JSON stream. |

## Best Practices

- **Sample Rate**: Ensure your PCM audio is sampled at 16,000 Hz, mono, 16-bit little-endian.
- **Chunk Size**: Send audio in chunks of approximately 100-200ms of audio data for optimal latency.
- **Connection Lifecycle**: Always handle `onClose` and `onError` events in your WebSocket client to provide a smooth user experience.
