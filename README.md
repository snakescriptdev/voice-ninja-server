System Requirements:
```bash
sudo apt-get install libsndfile1
```

# FASTAPI Run
```bash
uvicorn server:app --reload
```

## Environment Variables
Create a `.env` file in the root directory with the following configurations:

```env
# Google API Configuration
GOOGLE_API_KEY=your_google_api_key_here
```

### Required Environment Variables
| Variable | Description | Required |
|----------|-------------|----------|
| GOOGLE_API_KEY | API key for Google services integration | Yes |

**Note:** Never commit your actual API keys to version control. The values shown above are just examples.

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


## Python Version Requirements

### Recommended Python Version
- Python 3.12.7 is recommended for optimal performance and compatibility

To check your Python version:
```bash
python --version
```

**Note:** For production use, please change the default credentials and implement proper security measures.