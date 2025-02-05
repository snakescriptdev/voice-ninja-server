let isPlaying = true;
let lastMessageTime = 0;
const SAMPLE_RATE = 16000;
const NUM_CHANNELS = 1;
const PLAY_TIME_RESET_THRESHOLD_MS = 1.0;
let Frame = null;
let audioContext = null;
// AudioContext play time.
let playTime = 0;
const proto = protobuf.load('static/frame.proto', (err, root) => {
    if (err) {
        console.error("Error loading protobuf schema", err);
        throw err;
    }
    Frame = root.lookupType('Frame');
});

audioContext = new (window.AudioContext || window.webkitAudioContext)({
    latencyHint: 'interactive',
    sampleRate: SAMPLE_RATE
});

class WebSocketClient {
    constructor() {
        this.ws = null;
        this.statusIndicator = document.querySelector('.status-indicator');
        this.statusText = document.getElementById('connection-status');
        this.logContainer = document.getElementById('log-container');
        this.connectBtn = document.getElementById('connect-btn');
        this.disconnectBtn = document.getElementById('disconnect-btn');
        
        // Add new message input elements
        this.jsonInput = document.getElementById('json-input');
        this.formatBtn = document.getElementById('format-btn');
        this.sendBtn = document.getElementById('send-btn');
        
        // Remove enableAudioBtn since we won't need it
        this.audioContext = audioContext;
        this.initAudioContext();
        
        this.audioQueue = [];
        this.isPlaying = false;
        
        // Add audio processing properties
        this.audioWorkletNode = null;
        this.audioProcessor = null;
        this.sampleRate = SAMPLE_RATE; // Match the sample rate from TwilioFrameSerializer
        
        // Add new audio configuration
        this.BUFFER_SIZE = 512;
        this.latencyHint = 'interactive';
        this.playTime = playTime;
        this.PLAY_TIME_RESET_THRESHOLD_MS = PLAY_TIME_RESET_THRESHOLD_MS;
        
        this.setupEventListeners();
    }
    
    setupEventListeners() {
        // Modified user interaction handler
        document.addEventListener('click', async () => {
            if (!this.audioContext) {
                // Create AudioContext on first click
                this.audioContext = new AudioContext();
                this.log('Audio context created after user interaction', 'info');
            } else if (this.audioContext.state === 'suspended') {
                await this.audioContext.resume();
                this.log('Audio resumed after user interaction', 'info');
            }
        }, { once: true }); // Only handle first click
        
        this.connectBtn.addEventListener('click', () => this.connect());
        this.disconnectBtn.addEventListener('click', () => this.disconnect());
        
        // Replace message input event listener with JSON input
        this.formatBtn.addEventListener('click', () => this.formatJSON());
        this.sendBtn.addEventListener('click', () => this.sendMessage());
        
        // Remove the enable audio button listener since we're auto-enabling
    }
    
    log(message, type = 'info') {
        const entry = document.createElement('div');
        entry.className = `log-entry log-${type}`;
        entry.textContent = `${new Date().toLocaleTimeString()}: ${message}`;
        this.logContainer.appendChild(entry);
        this.logContainer.scrollTop = this.logContainer.scrollHeight;
    }
    
    updateStatus(status, message) {
        this.statusIndicator.className = `status-indicator ${status}`;
        this.statusText.textContent = message;
    }
    
    getAuthHeader() {
        const username = document.getElementById('username').value;
        const password = document.getElementById('password').value;
        return 'Basic ' + btoa(`${username}:${password}`);
    }
    
    async initAudioContext() {
        try {
            window.AudioContext = window.AudioContext || window.webkitAudioContext;
            this.log('Audio system initialized', 'info');
            
            // Initialize audio processing when context is created
            if (this.audioContext) {
                await this.setupAudioProcessing();
            }
        } catch (error) {
            this.log(`Audio initialization failed: ${error.message}`, 'error');
        }
    }
    
    
    async connect() {
        try {
            // Initialize audio context but don't force resume
            if (!this.audioContext) {
                await this.initAudioContext();
            }
            
            this.updateStatus('connecting', 'Connecting...');
            this.log('Attempting to connect...');
            
            // Create WebSocket connection with authorization header as query parameter
            const authHeader = this.getAuthHeader();
            const wsUrl = `ws://localhost:8000/ws?authorization=${encodeURIComponent(authHeader)}`;
            this.ws = new WebSocket(wsUrl);
            this.ws.binaryType = 'arraybuffer';
            
            this.ws.onopen = () => {
                this.updateStatus('connected', 'Connected');
                this.log('Connected successfully!');
                this.connectBtn.disabled = true;
                this.disconnectBtn.disabled = false;
                this.formatBtn.disabled = false;
                this.sendBtn.disabled = false;
            };
            
            this.ws.onmessage = async (event) => {
                try {
                    handleWebSocketMessage(event);
                } catch (error) {
                    this.log(`Error handling message: ${error.message}`, 'error');
                }
            };

            
            this.ws.onerror = (error) => {
                this.updateStatus('error', 'Error');
                this.log(`WebSocket Error: ${error.message}`, 'error');
            };
            
            this.ws.onclose = () => {
                this.updateStatus('', 'Disconnected');
                this.log('Connection closed');
                this.connectBtn.disabled = false;
                this.disconnectBtn.disabled = true;
                this.formatBtn.disabled = true;
                this.sendBtn.disabled = true;
            };
            
        } catch (error) {
            this.updateStatus('error', 'Error');
            this.log(`Connection Error: ${error.message}`, 'error');
        }
    }
    
    disconnect() {
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
    }
    
    formatJSON() {
        try {
            const jsonText = this.jsonInput.value.trim();
            if (jsonText) {
                const parsed = JSON.parse(jsonText);
                this.jsonInput.value = JSON.stringify(parsed, null, 2);
                this.jsonInput.classList.remove('json-error');
            }
        } catch (error) {
            this.jsonInput.classList.add('json-error');
            this.log(`Invalid JSON: ${error.message}`, 'error');
        }
    }
    
    sendMessage() {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            this.log('Cannot send message: Not connected to server', 'error');
            return;
        }
        
        const jsonText = this.jsonInput.value.trim();
        if (jsonText) {
            try {
                // Validate JSON before sending
                const jsonData = JSON.parse(jsonText);
                this.ws.send(JSON.stringify(jsonData));
                this.log(`Sent: ${JSON.stringify(jsonData)}`, 'info');
                // Optionally clear the input after sending
                // this.jsonInput.value = '';
            } catch (error) {
                this.jsonInput.classList.add('json-error');
                this.log(`Failed to send message: Invalid JSON - ${error.message}`, 'error');
            }
        }
    }
}


function handleWebSocketMessage(event) {
    const arrayBuffer = event.data;
    if (isPlaying) {
        enqueueAudioFromProto(arrayBuffer);
    }
}

function enqueueAudioFromProto(arrayBuffer) {
    const parsedFrame = Frame.decode(new Uint8Array(arrayBuffer));
    if (!parsedFrame?.audio) {
        return false;
    }

    // Reset play time if it's been a while we haven't played anything.
    const diffTime = audioContext.currentTime - lastMessageTime;
    if ((playTime == 0) || (diffTime > PLAY_TIME_RESET_THRESHOLD_MS)) {
        playTime = audioContext.currentTime;
    }
    lastMessageTime = audioContext.currentTime;

    // We should be able to use parsedFrame.audio.audio.buffer but for
    // some reason that contains all the bytes from the protobuf message.
    const audioVector = Array.from(parsedFrame.audio.audio);
    const audioArray = new Uint8Array(audioVector);

    audioContext.decodeAudioData(audioArray.buffer, function (buffer) {
        const source = new AudioBufferSourceNode(audioContext);
        source.buffer = buffer;
        source.start(playTime);
        source.connect(audioContext.destination);
        playTime = playTime + buffer.duration;
    });
}

// Initialize the WebSocket client
const client = new WebSocketClient();