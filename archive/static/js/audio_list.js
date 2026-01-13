class AudioList {
    constructor() {
        this.container = document.getElementById('recordings-container');
        this.currentAudio = null;
        this.currentPlayingButton = null;
        this.modalBackdrop = document.getElementById('modalBackdrop');
        this.deleteModal = document.getElementById('deleteModal');
        this.confirmDeleteButton = document.getElementById('confirmDelete');
        this.pendingDeleteId = null;
        this.initialize();
    }

    async initialize() {
        await this.loadRecordings();
        // this.initializeFilters();
        this.bindEvents();
    }

    async loadRecordings() {
        try {
            const response = await fetch('{{ host }}/api/audio-files');
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
        const duration = parseFloat(recording.duration).toFixed(1);
        
        element.innerHTML = `
            <div class="recording-info">
                <div class="recording-metadata">
                    <div class="metadata-primary">
                        <div class="voice-badge">
                            <i class="fas fa-microphone-alt"></i>
                            <span>${recording.voice}</span>
                        </div>
                        <div class="duration-badge">
                            <i class="fas fa-clock"></i>
                            <span>${duration}s</span>
                        </div>
                        <div class="contact-info">
                            <div class="contact-badge">
                                <i class="fas fa-envelope"></i>
                                <span>${recording.email}</span>
                            </div>
                            <div class="contact-badge">
                                <i class="fas fa-phone-alt"></i>
                                <span>${recording.number}</span>
                            </div>
                        </div>
                        <div class="timestamp">
                            <i class="fas fa-calendar-alt"></i>
                            <span>${date}</span>
                        </div>
                    </div>
                </div>
            </div>
            <div class="recording-actions">
                <button class="action-btn play" onclick="audioList.togglePlayback('${recording.file_url}', this)">
                    <div class="btn-content">
                        <i class="fas fa-play"></i>
                        <span class="button-text">Play</span>
                    </div>
                    <div class="btn-backdrop"></div>
                </button>
                <button class="action-btn delete" onclick="audioList.deleteRecording('${recording.id}')">
                    <div class="btn-content">
                        <i class="fas fa-trash-alt"></i>
                        <span class="button-text">Delete</span>
                    </div>
                    <div class="btn-backdrop"></div>
                </button>
            </div>
        `;
        
        return element;
    }

    bindEvents() {
        // Close modal when clicking outside
        this.deleteModal.addEventListener('click', (e) => {
            if (e.target === this.deleteModal) {
                this.hideModal();
            }
        });

        // Close modal when clicking close button
        const closeButtons = this.deleteModal.querySelectorAll('[data-bs-dismiss="modal"]');
        closeButtons.forEach(button => {
            button.addEventListener('click', () => this.hideModal());
        });

        // Confirm delete button
        this.confirmDeleteButton.addEventListener('click', () => this.confirmDeleteRecording());
    }

    showModal() {
        document.body.classList.add('modal-open');
        this.modalBackdrop.style.display = 'block';
        this.deleteModal.classList.add('show');
        this.deleteModal.style.display = 'block';
    }

    hideModal() {
        document.body.classList.remove('modal-open');
        this.modalBackdrop.style.display = 'none';
        this.deleteModal.classList.remove('show');
        this.deleteModal.style.display = 'none';
    }

    async deleteRecording(sessionId) {
        this.pendingDeleteId = sessionId;
        this.showModal();
    }

    async confirmDeleteRecording() {
        try {
            const response = await fetch(`{{ host }}/api/audio-delete/${this.pendingDeleteId}`, {
                method: 'DELETE'
            });

            if (response.ok) {
                this.hideModal();
                await this.loadRecordings();
            } else {
                const error = await response.json();
                this.showError(error.error || 'Failed to delete recording');
            }
        } catch (error) {
            console.error('Error deleting recording:', error);
            this.showError('Failed to delete recording');
        } finally {
            this.pendingDeleteId = null;
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