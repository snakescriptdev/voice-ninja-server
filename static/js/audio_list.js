class AudioList {
    constructor() {
        this.container = document.getElementById('recordings-container');
        this.currentAudio = null;
        this.currentPlayingButton = null;
        this.initialize();
    }

    async initialize() {
        await this.loadRecordings();
        // this.initializeFilters();
    }

    async loadRecordings() {
        try {
            const response = await fetch('/api/audio-files/');
            const data = await response.json();
            
            if (data.audio_records) {
                this.renderRecordings(data.audio_records);
            }
        } catch (error) {
            console.error('Error loading recordings:', error);
            this.showError('Failed to load recordings');
        }
    }

    renderRecordings(recordings) {
        this.container.innerHTML = '';
        
        if (recordings.length === 0) {
            this.container.innerHTML = `
                <div class="recording-item">
                    <div class="recording-info">
                        <span>No recordings found</span>
                    </div>
                </div>
            `;
            return;
        }

        recordings.forEach(recording => {
            const recordingElement = this.createRecordingElement(recording);
            this.container.appendChild(recordingElement);
        });
    }

    createRecordingElement(recording) {
        const element = document.createElement('div');
        element.className = 'recording-item';
        
        const date = new Date(recording.created_at).toLocaleString();
        
        element.innerHTML = `
            <div class="recording-info">
                <span class="recording-title">${recording.file_name}</span>
                <div class="recording-metadata">
                    <span class="metadata-item">
                        <i class="fas fa-microphone"></i> ${recording.voice}
                    </span>
                    <span class="metadata-item">
                        <i class="fas fa-clock"></i> ${recording.duration}s
                    </span>
                    <span class="metadata-item">
                        <i class="fas fa-calendar"></i> ${date}
                    </span>
                    <span class="metadata-item">
                        <i class="fas fa-envelope"></i> ${recording.email}
                    </span>
                    <span class="metadata-item">
                        <i class="fas fa-phone"></i> ${recording.number}
                    </span>
                </div>
            </div>
            <div class="recording-actions">
                <button class="action-btn play" onclick="audioList.togglePlayback('${recording.file_url}', this)">
                    <i class="fas fa-play"></i>
                    <span class="button-text">Play</span>
                </button>
                <button class="action-btn delete" onclick="audioList.deleteRecording('${recording.id}')">
                    <i class="fas fa-trash"></i>
                    <span>Delete</span>
                </button>
            </div>
        `;
        
        return element;
    }

    async deleteRecording(sessionId) {
        if (!confirm('Are you sure you want to delete this recording?')) {
            return;
        }

        try {
            const response = await fetch(`/api/audio-delete/${sessionId}/`, {
                method: 'DELETE'
            });

            if (response.ok) {
                await this.loadRecordings();
            } else {
                const error = await response.json();
                this.showError(error.error || 'Failed to delete recording');
            }
        } catch (error) {
            console.error('Error deleting recording:', error);
            this.showError('Failed to delete recording');
        }
    }

    togglePlayback(url, button) {
        if (this.currentAudio && this.currentPlayingButton && this.currentPlayingButton !== button) {
            this.stopCurrentAudio();
        }

        if (this.currentAudio && this.currentPlayingButton === button) {
            if (this.currentAudio.paused) {
                this.currentAudio.play();
                this.updatePlayButton(button, true);
            } else {
                this.currentAudio.pause();
                this.updatePlayButton(button, false);
            }
            return;
        }

        this.currentAudio = new Audio(url);
        this.currentPlayingButton = button;

        this.currentAudio.play();
        this.updatePlayButton(button, true);

        this.currentAudio.onended = () => {
            this.updatePlayButton(button, false);
        };
    }

    stopCurrentAudio() {
        if (this.currentAudio) {
            this.currentAudio.pause();
            this.currentAudio.currentTime = 0;
            if (this.currentPlayingButton) {
                this.updatePlayButton(this.currentPlayingButton, false);
            }
        }
    }

    updatePlayButton(button, isPlaying) {
        const icon = button.querySelector('i');
        const buttonText = button.querySelector('.button-text');
        
        if (isPlaying) {
            icon.className = 'fas fa-pause';
            buttonText.textContent = 'Pause';
        } else {
            icon.className = 'fas fa-play';
            buttonText.textContent = 'Play';
        }
    }

    showError(message) {
        // You can implement a proper error notification system here
        alert(message);
    }

    initializeFilters() {
        const searchInput = document.getElementById('searchRecording');
        const voiceFilter = document.getElementById('voiceFilter');
        
        searchInput.addEventListener('input', () => this.filterRecordings());
        voiceFilter.addEventListener('change', () => this.filterRecordings());
    }

    filterRecordings() {
        const searchTerm = document.getElementById('searchRecording').value.toLowerCase();
        const selectedVoice = document.getElementById('voiceFilter').value;
        
        const filteredRecordings = this.recordings.filter(recording => {
            const matchesSearch = recording.filename.toLowerCase().includes(searchTerm);
            const matchesVoice = !selectedVoice || recording.voice === selectedVoice;
            return matchesSearch && matchesVoice;
        });
        
        this.renderRecordings(filteredRecordings);
    }
}

// Initialize the audio list
const audioList = new AudioList(); 