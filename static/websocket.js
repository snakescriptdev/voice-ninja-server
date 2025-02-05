class WebSocketClient {
    constructor() {
        // Update these constants for better performance
        this.SAMPLE_RATE = 24000; // Increased from 16000
        this.NUM_CHANNELS = 1;  // Changed from 2 to 1 for better streaming
        this.PLAY_TIME_RESET_THRESHOLD_MS = 0.5; // Reduced from 4.0 for faster reset
        this.BUFFER_SIZE = 2048; // Increased buffer size
        
        // Audio state
        this.isPlaying = true;
        this.lastMessageTime = 0;
        this.playTime = 0;
        this.Frame = null;
        this.audioContext = null;
        
        // Initialize protobuf
        this.initProtobuf();
        
        // DOM elements
        this.initDOMElements();
        
        // Audio setup
        this.initAudioSystem();
        
        // Setup remaining properties
        this.ws = null;
        this.audioQueue = [];
        this.audioWorkletNode = null;
        this.audioProcessor = null;

        // Microphone setup
        this.microphoneStream = null;
        this.scriptProcessor = null;
        this.source = null;
        
        this.setupEventListeners();
    }

    initProtobuf() {
        try{

            protobuf.load('static/frame.proto', (err, root) => {
                if (err) {
                    console.error("Error loading protobuf schema", err);
                    throw err;
                }
                this.Frame = root.lookupType('Frame');
            });
        } catch (error) {
            this.log(`Error loading protobuf schema: ${error.message}`, 'error');
        }
    }

    initDOMElements() {
        try{
            this.statusIndicator = document.querySelector('.status-indicator');
            this.statusText = document.getElementById('connection-status');
            this.logContainer = document.getElementById('log-container');
            this.connectBtn = document.getElementById('start-btn');
            this.disconnectBtn = document.getElementById('stop-btn');
        } catch (error) {
            this.log(`Error initializing DOM elements: ${error.message}`, 'error');
        }
    }

    initAudioSystem() {
        try{
            this.audioContext = new (window.AudioContext || window.webkitAudioContext)({
                latencyHint: 'interactive',
                sampleRate: this.SAMPLE_RATE
            });
        } catch (error) {
            this.log(`Error initializing audio system: ${error.message}`, 'error');
        }
    }
    
    setupEventListeners() {
        try{

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
            this.Frame = null;
        } catch (error) {
            this.log(`Error setting up event listeners: ${error.message}`, 'error');
        }
    }
    
    log(message, type = 'info') {
        const entry = document.createElement('div');
        entry.className = `log-entry log-${type}`;
        entry.textContent = `${new Date().toLocaleTimeString()}: ${message}`;
        this.logContainer.appendChild(entry);
        this.logContainer.scrollTop = this.logContainer.scrollHeight;
    }
    
    updateStatus(status, message) {
        try{
            this.statusIndicator.className = `status-indicator ${status}`;
            this.statusText.textContent = message;
        } catch (error) {
            this.log(`Error updating status: ${error.message}`, 'error');
        }
    }
    
    getAuthHeader() {
        try{
            const username = document.getElementById('username').value;
            const password = document.getElementById('password').value;
            return 'Basic ' + btoa(`${username}:${password}`);
        } catch (error) {
            this.log(`Error getting auth header: ${error.message}`, 'error');
        }
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
                if (!this.audioContext) {
                    this.initAudioContext();
                }
                this.handleWebSocketOpen();
            };
            
            this.ws.onmessage = async (event) => {
                try {
                    this.handleWebSocketMessage(event);
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
                this.stopAudio(false);
            };
            
        } catch (error) {
            this.updateStatus('error', 'Error');
            this.log(`Connection Error: ${error.message}`, 'error');
        }
    }
    
    disconnect() {
        try{
            if (this.ws) {
                this.ws.close();
                this.ws = null;
                this.stopAudio(true);
            }
        } catch (error) {
            this.log(`Error disconnecting: ${error.message}`, 'error');
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
        try{

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
        } catch (error) {
            this.log(`Error sending message: ${error.message}`, 'error');
        }
    }

    handleWebSocketMessage(event) {
        try{
            const arrayBuffer = event.data;
            if (this.isPlaying) {
                this.enqueueAudioFromProto(arrayBuffer);
            }
        } catch (error) {
            this.log(`Error handling WebSocket message: ${error.message}`, 'error');
        }
    }

    enqueueAudioFromProto(arrayBuffer) {
        try{
            const parsedFrame = this.Frame.decode(new Uint8Array(arrayBuffer));
            if (!parsedFrame?.audio) {
                return false;
            }

            const currentTime = this.audioContext.currentTime;
            if (this.playTime < currentTime) {
                this.playTime = currentTime;
            }

            const audioVector = Array.from(parsedFrame.audio.audio);
            const audioArray = new Uint8Array(audioVector);

            this.audioContext.decodeAudioData(audioArray.buffer, (buffer) => {
                const source = new AudioBufferSourceNode(this.audioContext, {
                    playbackRate: 1.0 // Ensure normal playback rate
                });
                source.buffer = buffer;
            
                const scheduleDelay = 0.05; // 50ms scheduling delay
                const startTime = Math.max(this.playTime, currentTime + scheduleDelay);
            
                source.start(startTime);
                source.connect(this.audioContext.destination);
            
                this.playTime = startTime + buffer.duration;
            }).catch(error => {
                this.log(`Audio decoding error: ${error}`, 'error');
            });
        } catch (error) {
            this.log(`Error enqueuing audio from proto: ${error.message}`, 'error');
        }
    }

    handleWebSocketOpen(event) {
        try{
            console.log('WebSocket connection established.', event)

            navigator.mediaDevices.getUserMedia({
                audio: {
                    sampleRate: this.SAMPLE_RATE,
                    channelCount: this.NUM_CHANNELS,
                    autoGainControl: true,
                    echoCancellation: true,
                    noiseSuppression: true,
                }
            }).then((stream) => {
                this.microphoneStream = stream;
                // 512 is closest thing to 200ms.
                this.scriptProcessor = this.audioContext.createScriptProcessor(512, 1, 1);
                this.source = this.audioContext.createMediaStreamSource(stream);
                this.source.connect(this.scriptProcessor);
                this.scriptProcessor.connect(this.audioContext.destination);

                this.scriptProcessor.onaudioprocess = (event) => {
                    if (!this.ws) {
                        return;
                    }

                    const audioData = event.inputBuffer.getChannelData(0);
                    const pcmS16Array = this.convertFloat32ToS16PCM(audioData);
                    const pcmByteArray = new Uint8Array(pcmS16Array.buffer);
                    const frame = this.Frame.create({
                        audio: {
                            audio: Array.from(pcmByteArray),
                            sampleRate: this.SAMPLE_RATE,
                            numChannels: this.NUM_CHANNELS
                        }
                    });
                    const encodedFrame = new Uint8Array(this.Frame.encode(frame).finish());
                    this.ws.send(encodedFrame);
                };
            }).catch((error) => console.error('Error accessing microphone:', error));
        } catch (error) {
            this.log(`Error accessing microphone: ${error.message}`, 'error');
        }
    }
    
    convertFloat32ToS16PCM(float32Array) {
        try{
            let int16Array = new Int16Array(float32Array.length);

            for (let i = 0; i < float32Array.length; i++) {
                let clampedValue = Math.max(-1, Math.min(1, float32Array[i]));
                int16Array[i] = clampedValue < 0 ? clampedValue * 32768 : clampedValue * 32767;
            }
            return int16Array;
        } catch (error) {
            this.log(`Error converting float32 to s16pcm: ${error.message}`, 'error');
        }
    }

    stopAudio(closeWebsocket) {
        try{
            this.playTime = 0;
            this.isPlaying = false;

            if (this.ws && closeWebsocket) {
                this.ws.close();
                this.ws = null;
            }

            // Properly cleanup audio resources
            if (this.scriptProcessor) {
                this.scriptProcessor.disconnect();
                this.scriptProcessor = null;
            }
            
            if (this.source) {
                this.source.disconnect();
                this.source = null;
            }

            // Stop all microphone tracks
            if (this.microphoneStream) {
                this.microphoneStream.getTracks().forEach(track => track.stop());
                this.microphoneStream = null;
            }
        } catch (error) {
            this.log(`Error stopping audio: ${error.message}`, 'error');
        }
    }


}

// Initialize the WebSocket client
const client = new WebSocketClient();