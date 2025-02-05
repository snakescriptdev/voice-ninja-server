# FASTAPI Run
```bash
uvicorn server:app --reload
```

## WebSocket Connection
The application provides a WebSocket endpoint for real-time communication.

### Endpoint
```
ws://localhost:8000/ws
```

### Authentication
The WebSocket connection requires Basic Authentication.

#### Headers
```
authorization: Basic base64(username:password)
```

### Connection Example
```javascript
// JavaScript WebSocket connection example
const ws = new WebSocket('ws://localhost:8000/ws?authorization=Basic YWRtaW46YWRtaW4xMjM=');

ws.onopen = () => {
    console.log('Connected to WebSocket');
};

ws.onmessage = (event) => {
    console.log('Received:', event.data);
};

ws.onerror = (error) => {
    console.error('WebSocket error:', error);
};
```

### Default Credentials
- Username: `admin`
- Password: `admin123`

**Note:** For production use, please change the default credentials and implement proper security measures.