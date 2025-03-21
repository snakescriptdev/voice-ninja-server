async function loadProtobufLibrary() {
    const protobufmodule = await import('https://cdn.jsdelivr.net/npm/protobufjs@7.X.X/dist/protobuf.min.js');
    return true;
}

class WebSocketClient {
    constructor(agentId = null) {
        // Add base URL configuration
        this.BASE_URL = window.location.host;

        this.agentId = agentId;
        this.uid = null;
        // Update these constants for better performance
        this.SAMPLE_RATE = 16000; // Increased from 16000
        this.NUM_CHANNELS = 1;  // Changed from 2 to 1 for better streaming
        
        // Audio state
        this.isPlaying = true;
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

    loadProtobufLibrary() {
        this.loadProtobufLibrary(() => {
            this.initProtobuf();
        });
    }
        // const script = document.createElement('script');


    async initProtobuf() {
        try{
            // debugger;
            await loadProtobufLibrary();
            protobuf.load('http://dev.voiceninja.ai/static/frame.proto', (err, root) => {
                if (err) {
                    console.error("Error loading protobuf schema", err);
                    throw err;
                }
                this.Frame = root.lookupType('Frame');
                if (!this.Frame) {
                    console.error("Frame type not found in protobuf schema");
                }
            });
        } catch (error) {
            this.log(`Error loading protobuf schema: ${error.message}`, 'error');
        }
    }

    initDOMElements() {
        try{
            // this.statusIndicator = document.querySelector('.status-indicator');
            // this.statusText = document.getElementById('connection-status');
            // this.logContainer = document.getElementById('log-container');
            this.connectBtn = document.getElementById('startCall');
            this.disconnectBtn = document.getElementById('endCallPopup');
            // this.languageSelect = document.getElementById('language-select');
            // this.audioListBtn = document.getElementById('audio-list-btn');

            // this.audioListBtn.addEventListener('click', () => {
            //     window.location.href = '/audio_list/';
            // });
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
            // document.addEventListener('click', async () => {
            //     if (!this.audioContext) {
            //         // Create AudioContext on first click
            //         this.audioContext = new AudioContext();
            //         this.log('Audio context created after user interaction', 'info');
            //     } else if (this.audioContext.state === 'suspended') {
            //         await this.audioContext.resume();
            //         this.log('Audio resumed after user interaction', 'info');
            //     }
            // }, { once: true }); // Only handle first click
            
            this.connectBtn.addEventListener('click', () => this.connect());
            this.disconnectBtn.addEventListener('click', () => this.disconnect());
            // this.Frame = null;
        } catch (error) {
            this.log(`Error setting up event listeners: ${error.message}`, 'error');
        }
    }
    
    log(message, type = 'info') {
        const entry = document.createElement('div');
        entry.className = `log-entry log-${type}`;
        const timestamp = new Date().toLocaleTimeString('en-US', {
            hour: '2-digit',
            minute: '2-digit',
            hour12: true
        });
        entry.textContent = `${timestamp}: ${message}`;
        // this.logContainer.appendChild(entry);
        // this.logContainer.scrollTop = this.logContainer.scrollHeight;
    }
    
    updateStatus(status, message) {
        try {
            // const statusPanel = document.querySelector('.status-panel');
            // const statusText = document.getElementById('connection-status');
            
            // // Remove all previous status classes
            // statusPanel.classList.remove('connecting', 'connected', 'error');
            
            // // Add the new status class
            // if (status) {
            //     statusPanel.classList.add(status);
            // }
            
            // // Update the status text
            // if (statusText) {
            //     statusText.textContent = message;
            // }
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
    
    get_voice(){
        try{
            const voice = this.languageSelect.value;
            return voice;
        } catch (error) {
            this.log(`Error getting voice: ${error.message}`, 'error');
            return "";
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
            // If already connected, disconnect instead
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.disconnect();
                // this.connectBtn.innerHTML = '<img src="http://dev.voiceninja.ai/static/Web/images/no-microphone.gif" style="width: 35px; height: 35px; object-fit: cover; vertical-align: middle; margin-right: 5px;">';
                return;
            }

            this.updateStatus('connecting', 'Connecting...');
            this.log('Attempting to connect...');
            // this.connectBtn.innerHTML = '<img src="http://dev.voiceninja.ai/static/Web/images/microphone.gif" style="width: 35px; height: 35px; object-fit: cover; vertical-align: middle; margin-right: 5px;">';
            
            // Use dynamic WebSocket URL
            // const authHeader = this.getAuthHeader();
            // const voice = this.get_voice();
            const wsUrl = `ws://${this.BASE_URL}/ws/agent_ws/?agent_id=${this.agentId}`;
                this.ws = new WebSocket(wsUrl); 
            this.ws.binaryType = 'arraybuffer';
            
            this.ws.onopen = () => {
                this.updateStatus('connected', 'Connected');
                this.log('Connected successfully!', 'info');
                // this.log('Selected Voice: ' + voice, 'info');
                // this.connectBtn.disabled = true;
                // this.connectBtn.hidden = true;
                // this.disconnectBtn.disabled = false;
                // this.disconnectBtn.hidden = false;
                // this.languageSelect.disabled = true;
                if (!this.audioContext) {
                    this.initAudioContext();
                }
                this.handleWebSocketOpen();
            };
            
            this.ws.onmessage = async (event) => {
                try {
                    // if (!this.uid) {
                    //     const message = JSON.parse(event.data);
                    //     if (message.type === "UID") {
                    //         this.uid = message.uid;
                    //         this.log(`UID: ${this.uid}`, 'info');
                    //     }
                    // }
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
                this.log('Connection closed', 'error');
                this.stopAudio(false);
                this.uid = null;
                window.location.reload();
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
                this.log(`Audio decoding error: ${error} `, 'error');
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
// const client = new WebSocketClient();

function toggleRecorder() {
    const recorderControls = document.getElementById("recorderControls");
    const startCall = document.getElementById("startCall");

    if (recorderControls.classList.contains("hidden")) {
        recorderControls.classList.remove("hidden");
        recorderControls.classList.add("show");
        startCall.style.display = "none"; // Hide the voice icon button
    } else {
        recorderControls.classList.remove("show");
        recorderControls.classList.add("hidden");
        startCall.style.display = "block"; // Show the voice icon button again
    }
}


function stopRecorder() {
    const recorderControls = document.getElementById('recorderControls');
    const voiceIcon = document.querySelector('.whatsapp_outer_mobile');

    recorderControls.classList.remove('show');
    setTimeout(() => {
        recorderControls.classList.add('hidden');
        voiceIcon.style.display = 'flex'; 
        voiceIcon.style.opacity = '1';
    }, 500);
}
