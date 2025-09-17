/**
 * ElevenLabs WebSocket Client for Voice Ninja
 * Handles connection to ElevenLabs Conversational AI via WebSocket
 */
class ElevenLabsWebSocketClient {
    constructor(agentId = null, language = null, model = null) {
        this.agentId = agentId;
        this.language = language || 'en'; // Default to English if not specified
        this.model = model; // Store the model for override
        this.BASE_URL = window.location.host;
        this.isRecording = false;
        this.isMuted = false;
        this.isConnected = false;
        this.isConnecting = false;
        
        // Audio configuration for ElevenLabs (16kHz, mono, 16-bit)
        this.SAMPLE_RATE = 16000;
        this.NUM_CHANNELS = 1;
        
        this.ws = null;
        this.audioContext = null;
        this.microphone = null;
        this.audioProcessor = null;
        
        // Conversation state
        this.conversationReady = false;
        this.audioInterfaceReady = false;
        
        // Audio playback
        this.audioQueue = [];
        this.isPlaying = false;
        
        // DOM elements
        this.initDOMElements();
        
        // Initialize audio system
        this.initAudioSystem();
        
        // Set up event listeners
        this.setupEventListeners();
        
        // Make client globally available
        window.elevenLabsClient = this;
        
        
    }

    initDOMElements() {
        this.connectBtn = document.getElementById('elevenLabsStartCall');
        this.disconnectBtn = document.getElementById('elevenLabsEndCall');
        this.muteBtn = document.getElementById('elevenLabsMuteBtn');
        this.statusText = document.getElementById('elevenlabs-status-text');
        this.connectionStatus = document.getElementById('elevenlabs-connection-status');
        this.transcript = document.getElementById('elevenlabs-transcript');
        this.previewStatus = document.getElementById('preview-status');
        
        if (this.previewStatus) {
            this.previewStatus.textContent = 'Initialized';
        }
    }

    async initAudioSystem() {
        try {
            this.audioContext = new (window.AudioContext || window.webkitAudioContext)({
                latencyHint: 'interactive',
                sampleRate: this.SAMPLE_RATE
            });
            
            // Initialize user interaction for audio playback
            this.audioUnlocked = false;
            
            // Initialize FIFO audio queue
            this.audioQueue = [];
            this.isPlayingAudio = false;
            this.currentAudioSource = null;
            
            // Voice activity detection for interruption
            this.voiceActivityThreshold = 0.01; // Minimum volume to consider as speech
            this.consecutiveActiveFrames = 0;
            this.requiredActiveFrames = 5; // Frames needed to trigger interruption
            
            
        } catch (error) {
            console.error('Failed to initialize audio system:', error);
            this.updateStatus('error', 'Audio initialization failed');
        }
    }

    setupEventListeners() {
        if (this.connectBtn) {
            this.connectBtn.addEventListener('click', () => this.connect());
        }
        
        if (this.disconnectBtn) {
            this.disconnectBtn.addEventListener('click', () => this.disconnect());
        }
        
        if (this.muteBtn) {
            this.muteBtn.addEventListener('click', () => this.toggleMute());
        }
    }

    async connect() {
        try {
            if (this.isConnected || this.isConnecting) {
                
                return;
            }

            this.isConnecting = true;
            this.updateStatus('connecting', 'Connecting to AI Agent...');
            
            // Unlock audio playback on user interaction
            await this.unlockAudioPlayback();
            
            // Request microphone access
            await this.requestMicrophoneAccess();
            
            // Connect to ElevenLabs WebSocket
            await this.connectWebSocket();
            
        } catch (error) {
            console.error('Failed to connect to AI Agent:', error);
            this.updateStatus('error', 'Connection failed');
            this.showError('Failed to connect: ' + error.message);
        } finally {
            this.isConnecting = false;
        }
    }

    async unlockAudioPlayback() {
        if (this.audioUnlocked) return;
        
        try {
            // Resume audio context to unlock audio playback
            if (this.audioContext.state === 'suspended') {
                await this.audioContext.resume();
            }
            
            // Play a silent buffer to unlock audio
            const buffer = this.audioContext.createBuffer(1, 1, this.SAMPLE_RATE);
            const source = this.audioContext.createBufferSource();
            source.buffer = buffer;
            source.connect(this.audioContext.destination);
            source.start();
            
            this.audioUnlocked = true;
            
        } catch (error) {
            console.warn('Could not unlock audio playback:', error);
        }
    }

    async requestMicrophoneAccess() {
        try {
            this.microphone = await navigator.mediaDevices.getUserMedia({
                audio: {
                    sampleRate: this.SAMPLE_RATE,
                    channelCount: this.NUM_CHANNELS,
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true
                }
            });
            
            
            return true;
        } catch (error) {
            throw new Error('Microphone access denied or unavailable');
        }
    }

    async connectWebSocket() {
        return new Promise((resolve, reject) => {
            try {
                const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                const wsUrl = `${wsProtocol}//${this.BASE_URL}/elevenlabs/live/ws/${this.agentId}`;
                

                
                this.ws = new WebSocket(wsUrl);
                this.ws.binaryType = 'arraybuffer';
                
                this.ws.onopen = () => {
                    
                    this.isConnected = true;
                    this.isConnecting = false;
                    this.updateStatus('connected', 'Connected - Waiting for conversation...');
                    
                    // Send language preference to initialize conversation
                    const initData = {
                        type: 'conversation_init',
                        language: this.language
                    };
                    
                    // Add model override based on language selection
                    const ENGLISH_CODES = ["en", "en-US", "en-GB"];
                    const EN_MODELS = ["eleven_turbo_v2", "eleven_flash_v2"];
                    const NON_EN_MODELS = ["eleven_turbo_v2_5", "eleven_flash_v2_5"];
                    
                    let selectedModel = this.model; // Use provided model if available
                    if (!selectedModel) {
                        if (this.language && ENGLISH_CODES.includes(this.language)) {
                            // For English, prefer eleven_turbo_v2 if available
                            selectedModel = "eleven_turbo_v2";
                        } else {
                            // For non-English, use eleven_turbo_v2_5
                            selectedModel = "eleven_turbo_v2_5";
                        }
                    }
                    
                    initData.model = selectedModel;
                   
                    this.ws.send(JSON.stringify(initData));
                    
                    // Don't start audio streaming immediately, wait for conversation_ready
                    resolve();
                };
                
                this.ws.onmessage = (event) => {
                    this.handleWebSocketMessage(event);
                };
                
                this.ws.onclose = (event) => {
     
                    this.isConnected = false;
                    this.isConnecting = false;
                    this.conversationReady = false;
                    this.audioInterfaceReady = false;
                    this.updateStatus('disconnected', 'Disconnected');
                    this.stopAudioStreaming();
                };
                
                this.ws.onerror = (error) => {
                    
                    reject(new Error('WebSocket connection failed'));
                };
                
                // Connection timeout
                setTimeout(() => {
                    if (!this.isConnected) {
                        reject(new Error('Connection timeout'));
                    }
                }, 10000);
                
            } catch (error) {
                reject(error);
            }
        });
    }

    handleWebSocketMessage(event) {
        try {
            if (typeof event.data === 'string') {
                // JSON message (transcript, status, etc.)
                const message = JSON.parse(event.data);
                this.handleJSONMessage(message);
            } else {
                // Binary audio data
                this.handleAudioMessage(event.data);
            }
        } catch (error) {
            console.error('Error handling WebSocket message:', error);
        }
    }

    handleJSONMessage(message) {
        switch (message.type) {
            case 'conversation_ready':
                
                this.conversationReady = true;
                this.updateStatus('connected', 'Conversation ready - Waiting for audio interface...');
                break;
                
            case 'audio_interface_ready':
                
                this.audioInterfaceReady = true;
                this.updateStatus('connected', 'Ready - Speak now!');
                if (this.conversationReady) {
                    this.startAudioStreaming();
                }
                break;
                
            case 'language_confirmed':
                
                this.updateStatus('connected', `Language set to ${message.language} - Connecting...`);
                break;
                
            case 'audio_chunk':
                // Handle base64 encoded audio
                if (message.data_b64) {

                    const audioData = this.base64ToArrayBuffer(message.data_b64);
                    
                    this.queueAudio(audioData);
                } else {
                    console.warn('Received audio_chunk without data_b64');
                }
                break;
                
            case 'user_transcript':
                this.addToTranscript('user', message.text);
                break;
                
            case 'agent_response':
                this.addToTranscript('agent', message.text);
                break;
                
            case 'latency_measurement':
                
                break;
                
            case 'error':
                console.error('AI Agent error:', message.message);
                this.showError(message.message);
                break;
                
            case 'session_replaced':
                
                break;
                
            default:
                console.log('Unknown message type:', message.type);
        }
    }

    handleAudioMessage(audioData) {
        // Handle binary audio data from AI Agent
        this.playAudio(audioData);
    }

    // FIFO Audio Queue Management
    queueAudio(audioData) {
        
        this.audioQueue.push(audioData);
        
        // Update status to show queuing
        if (this.audioQueue.length > 1) {
            this.updateStatus('playing', `Playing audio (${this.audioQueue.length} chunks queued)`);
        }
        
        // Start playing if not already playing
        if (!this.isPlayingAudio) {
            this.processAudioQueue();
        }
    }

    async processAudioQueue() {
        if (this.audioQueue.length === 0) {
            this.isPlayingAudio = false;
            this.updateStatus('ready', 'Ready - Speak now!');

            return;
        }

        this.isPlayingAudio = true;
        const audioData = this.audioQueue.shift(); // FIFO - get first item
        
        try {
            await this.playAudioChunk(audioData);
        } catch (error) {
            console.error('Error playing audio chunk:', error);
        }
        
        // Continue processing queue
        setTimeout(() => this.processAudioQueue(), 10); // Small delay to prevent blocking
    }

    clearAudioQueue() {

        this.audioQueue = [];
        
        // Stop current audio if playing
        if (this.currentAudioSource) {
            try {
                this.currentAudioSource.stop();
                this.currentAudioSource = null;
            } catch (error) {
                console.warn('Error stopping current audio source:', error);
            }
        }
        
        this.isPlayingAudio = false;
        this.consecutiveActiveFrames = 0; // Reset voice activity counter
    }

    // Method to adjust voice activity detection sensitivity
    setInterruptionSensitivity(threshold = 0.01, requiredFrames = 5) {
        this.voiceActivityThreshold = threshold;
        this.requiredActiveFrames = requiredFrames;
        
    }

    async playAudioChunk(audioData) {
        return new Promise((resolve, reject) => {
            try {
                
                
                if (!this.audioContext) {
                    console.warn('Audio context not available');
                    resolve();
                    return;
                }

                // Resume audio context if suspended
                if (this.audioContext.state === 'suspended') {
                    
                    this.audioContext.resume().then(() => {
                        this.playAudioInternal(audioData, resolve, reject);
                    });
                } else {
                    this.playAudioInternal(audioData, resolve, reject);
                }
                
            } catch (error) {
                console.error('Error in playAudioChunk:', error);
                reject(error);
            }
        });
    }

    playAudioInternal(audioData, resolve, reject) {
        try {
            

            // For PCM data, we need to create an AudioBuffer directly
            const audioBuffer = this.audioContext.createBuffer(
                1, // mono
                audioData.byteLength / 2, // 16-bit samples = 2 bytes per sample
                this.SAMPLE_RATE
            );
            
            // Convert Int16 to Float32 for Web Audio API
            const channelData = audioBuffer.getChannelData(0);
            const int16View = new Int16Array(audioData);
            
            for (let i = 0; i < int16View.length; i++) {
                // Convert int16 [-32768, 32767] to float32 [-1, 1]
                channelData[i] = int16View[i] / 32768.0;
            }
            
            const source = this.audioContext.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(this.audioContext.destination);
            
            // Store reference to current audio source
            this.currentAudioSource = source;
            
            // Set up completion callback
            source.onended = () => {
                
                this.currentAudioSource = null;
                resolve();
            };
            
            source.start();
            
            
            
        } catch (error) {
            console.error('Error in playAudioInternal:', error);
            reject(error);
        }
    }

    startAudioStreaming() {
        try {
            if (!this.microphone || !this.audioContext) {
                throw new Error('Microphone or audio context not available');
            }

            // Create audio source from microphone
            const source = this.audioContext.createMediaStreamSource(this.microphone);
            
            // Create ScriptProcessor for real-time PCM audio processing
            this.audioProcessor = this.audioContext.createScriptProcessor(4096, 1, 1);
            
            this.audioProcessor.onaudioprocess = (event) => {
                if (this.ws && this.ws.readyState === WebSocket.OPEN && !this.isMuted && 
                    this.conversationReady && this.audioInterfaceReady) {
                    
                    const inputBuffer = event.inputBuffer;
                    const inputData = inputBuffer.getChannelData(0);
                    
                    // Voice activity detection for interruption
                    const rms = Math.sqrt(inputData.reduce((sum, sample) => sum + sample * sample, 0) / inputData.length);
                    
                    if (rms > this.voiceActivityThreshold) {
                        this.consecutiveActiveFrames++;
                        
                        // Only clear queue if agent is speaking AND user has been speaking consistently
                        if (this.isPlayingAudio && this.consecutiveActiveFrames >= this.requiredActiveFrames) {
                            
                            this.clearAudioQueue();
                            this.consecutiveActiveFrames = 0; // Reset after interruption
                        }
                    } else {
                        this.consecutiveActiveFrames = 0; // Reset if silence
                    }
                    
                    // Convert float32 to int16 PCM (ElevenLabs format)
                    const pcmData = new Int16Array(inputData.length);
                    for (let i = 0; i < inputData.length; i++) {
                        // Convert float [-1,1] to int16 [-32768,32767]
                        pcmData[i] = Math.max(-32768, Math.min(32767, inputData[i] * 32767));
                    }
                    
                    // Convert to base64 and send
                    const uint8Array = new Uint8Array(pcmData.buffer);
                    const base64Audio = btoa(String.fromCharCode.apply(null, uint8Array));
                    this.sendAudioChunk(base64Audio);
                }
            };
            
            // Connect audio nodes
            source.connect(this.audioProcessor);
            this.audioProcessor.connect(this.audioContext.destination);
            
            this.isRecording = true;
            
            
        } catch (error) {
            console.error('Failed to start audio streaming:', error);
            this.showError('Failed to start audio streaming');
        }
    }

    sendAudioChunk(base64Audio) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN && !this.isMuted) {
            const message = {
                type: 'user_audio_chunk',
                data_b64: base64Audio
            };
            this.ws.send(JSON.stringify(message));
        }
    }

    stopAudioStreaming() {
        try {
            if (this.audioProcessor) {
                this.audioProcessor.disconnect();
                this.audioProcessor = null;
                this.isRecording = false;
            }
            
            if (this.microphone) {
                this.microphone.getTracks().forEach(track => track.stop());
                this.microphone = null;
            }
            
            
            
        } catch (error) {
            console.error('Error stopping audio streaming:', error);
        }
    }

    disconnect() {
        try {
            this.stopAudioStreaming();
            
            // Clear audio queue to prevent overlapping playback
            this.clearAudioQueue();
            
            if (this.ws) {
                if (this.ws.readyState === WebSocket.OPEN) {
                    this.ws.send(JSON.stringify({ type: 'end' }));
                }
                this.ws.close();
                this.ws = null;
            }
            
            this.isConnected = false;
            this.isConnecting = false;
            this.conversationReady = false;
            this.audioInterfaceReady = false;
            this.updateStatus('disconnected', 'Disconnected');
            
            
            
        } catch (error) {
            console.error('Error during disconnect:', error);
        }
    }

    toggleMute() {
        this.isMuted = !this.isMuted;
        
        
        if (this.muteBtn) {
            this.muteBtn.textContent = this.isMuted ? '🔇 Muted' : '🔊 Unmuted';
        }
        
        return this.isMuted;
    }

    updateStatus(state, message) {
        if (this.connectionStatus) {
            this.connectionStatus.textContent = message;
            this.connectionStatus.className = `elevenlabs-status-indicator ${state}`;
        }
        
        if (this.previewStatus) {
            this.previewStatus.textContent = message;
        }
        
        
    }

    addToTranscript(speaker, text) {
        if (!this.transcript) return;
        
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${speaker}`;
        
        const speakerSpan = document.createElement('div');
        speakerSpan.className = 'speaker';
        speakerSpan.textContent = speaker.toUpperCase();
        
        const textDiv = document.createElement('div');
        textDiv.textContent = text;
        
        messageDiv.appendChild(speakerSpan);
        messageDiv.appendChild(textDiv);
        
        this.transcript.appendChild(messageDiv);
        this.transcript.scrollTop = this.transcript.scrollHeight;
    }

    showError(message) {
        console.error('ElevenLabs Error:', message);
        
        // Show error in transcript
        this.addToTranscript('system', `Error: ${message}`);
        
        // Update status
        this.updateStatus('error', 'Error occurred');
    }

    base64ToArrayBuffer(base64) {
        const binaryString = window.atob(base64);
        const bytes = new Uint8Array(binaryString.length);
        for (let i = 0; i < binaryString.length; i++) {
            bytes[i] = binaryString.charCodeAt(i);
        }
        return bytes.buffer;
    }
}

// Global functions for UI controls (called from injected HTML)
function toggleElevenLabsRecorder() {
    const recorderControls = document.getElementById("elevenLabsRecorderControls");
    const startCall = document.getElementById("elevenLabsStartCall");

    if (recorderControls.classList.contains("hidden")) {
        recorderControls.classList.remove("hidden");
        recorderControls.classList.add("show");
        startCall.style.display = "none";
        
        // Auto-connect when opening
        if (window.elevenLabsClient && !window.elevenLabsClient.isConnected) {
            window.elevenLabsClient.connect();
        }
    } else {
        recorderControls.classList.remove("show");
        recorderControls.classList.add("hidden");
        startCall.style.display = "block";
    }
}

function stopElevenLabsRecorder() {
    const recorderControls = document.getElementById('elevenLabsRecorderControls');
    const voiceIcon = document.getElementById('elevenLabsStartCall');

    recorderControls.classList.remove('show');
    setTimeout(() => {
        recorderControls.classList.add('hidden');
        voiceIcon.style.display = 'flex';
        voiceIcon.style.opacity = '1';
    }, 500);
    
    // Disconnect ElevenLabs client
    if (window.elevenLabsClient) {
        window.elevenLabsClient.disconnect();
    }
}

function toggleElevenLabsMute() {
    if (window.elevenLabsClient) {
        return window.elevenLabsClient.toggleMute();
    }
    return false;
}

