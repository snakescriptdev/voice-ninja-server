import numpy as np
import librosa
from typing import Optional, Tuple
import logging
from app.core.config import VoiceSettings

logger = logging.getLogger(__name__)

class AudioEnhancementProcessor:
    """
    Enhanced audio processor for noise reduction and voice quality improvement
    in noisy environments.
    """
    
    def __init__(self):
        self.config = VoiceSettings
        self.noise_profile = None
        self.speech_threshold = 0.1
        self.frame_length = int(self.config.AUDIO_SAMPLE_RATE * 0.025)  # 25ms frames
        self.hop_length = int(self.config.AUDIO_SAMPLE_RATE * 0.010)   # 10ms hop
        
    def process_audio_frame(self, audio_data: np.ndarray, is_user_audio: bool = True) -> np.ndarray:
        """
        Process a single audio frame with noise reduction and enhancement.
        
        Args:
            audio_data: Input audio data as numpy array
            is_user_audio: Whether this is user input audio (True) or assistant output (False)
            
        Returns:
            Enhanced audio data
        """
        try:
            if len(audio_data) == 0:
                return audio_data
                
            # Convert to float if needed
            if audio_data.dtype != np.float32:
                audio_data = audio_data.astype(np.float32)
            
            # Apply high-pass filter to remove low-frequency noise
            if self.config.AUDIO_HIGH_PASS_FILTER_FREQ > 0:
                audio_data = self._apply_high_pass_filter(audio_data)
            
            # Apply low-pass filter to remove high-frequency noise
            if self.config.AUDIO_LOW_PASS_FILTER_FREQ < self.config.AUDIO_SAMPLE_RATE // 2:
                audio_data = self._apply_low_pass_filter(audio_data)
            
            # Apply noise reduction for user audio
            if is_user_audio and self.config.AUDIO_NOISE_REDUCTION_ENABLED:
                audio_data = self._apply_noise_reduction(audio_data)
                
                # Apply echo cancellation
                if self.config.AUDIO_ECHO_CANCELLATION:
                    audio_data = self._apply_echo_cancellation(audio_data)
                
                # Apply automatic gain control
                if self.config.AUDIO_AGC_ENABLED:
                    audio_data = self._apply_agc(audio_data)
            
            # Ensure audio doesn't clip
            audio_data = np.clip(audio_data, -1.0, 1.0)
            
            return audio_data
            
        except Exception as e:
            logger.error(f"Error in audio enhancement: {e}")
            return audio_data
    
    def _apply_high_pass_filter(self, audio_data: np.ndarray) -> np.ndarray:
        """Apply high-pass filter to remove low-frequency noise."""
        try:
            # Simple high-pass filter using librosa
            cutoff = self.config.AUDIO_HIGH_PASS_FILTER_FREQ / (self.config.AUDIO_SAMPLE_RATE / 2)
            b, a = librosa.filters.butter(4, cutoff, btype='high')
            return librosa.filtfilt(b, a, audio_data)
        except Exception as e:
            logger.warning(f"High-pass filter failed: {e}")
            return audio_data
    
    def _apply_low_pass_filter(self, audio_data: np.ndarray) -> np.ndarray:
        """Apply low-pass filter to remove high-frequency noise."""
        try:
            # Simple low-pass filter using librosa
            cutoff = self.config.AUDIO_LOW_PASS_FILTER_FREQ / (self.config.AUDIO_SAMPLE_RATE / 2)
            b, a = librosa.filters.butter(4, cutoff, btype='low')
            return librosa.filtfilt(b, a, audio_data)
        except Exception as e:
            logger.warning(f"Low-pass filter failed: {e}")
            return audio_data
    
    def _apply_noise_reduction(self, audio_data: np.ndarray) -> np.ndarray:
        """Apply spectral noise reduction."""
        try:
            # Convert to frequency domain
            stft = librosa.stft(audio_data, n_fft=self.frame_length, hop_length=self.hop_length)
            
            # Estimate noise profile from first few frames (assuming they contain mostly noise)
            if self.noise_profile is None:
                self.noise_profile = np.mean(np.abs(stft[:, :10]), axis=1, keepdims=True)
            
            # Calculate signal-to-noise ratio
            signal_power = np.abs(stft) ** 2
            noise_power = np.abs(self.noise_profile) ** 2
            snr = signal_power / (noise_power + 1e-10)
            
            # Apply Wiener filter for noise reduction
            gain = snr / (snr + 1/self.config.AUDIO_NOISE_REDUCTION_STRENGTH)
            stft_enhanced = stft * gain
            
            # Convert back to time domain
            return librosa.istft(stft_enhanced, hop_length=self.hop_length)
            
        except Exception as e:
            logger.warning(f"Noise reduction failed: {e}")
            return audio_data
    
    def _apply_echo_cancellation(self, audio_data: np.ndarray) -> np.ndarray:
        """Simple echo cancellation using adaptive filtering."""
        try:
            # This is a simplified echo cancellation
            # In production, you might want to use more sophisticated methods
            
            # Apply a simple adaptive filter to reduce echo
            # This is a basic implementation - for production use, consider using
            # specialized echo cancellation libraries like WebRTC's AEC
            
            # Simple approach: reduce amplitude of repeated patterns
            if len(audio_data) > self.frame_length:
                # Look for potential echo patterns
                correlation = np.correlate(audio_data, audio_data, mode='full')
                if np.max(correlation) > 0.8:  # High correlation suggests echo
                    # Reduce amplitude slightly
                    audio_data *= 0.9
            
            return audio_data
            
        except Exception as e:
            logger.warning(f"Echo cancellation failed: {e}")
            return audio_data
    
    def _apply_agc(self, audio_data: np.ndarray) -> np.ndarray:
        """Apply automatic gain control."""
        try:
            # Calculate RMS level
            rms = np.sqrt(np.mean(audio_data ** 2))
            
            if rms > 0:
                # Convert to dB
                current_level_db = 20 * np.log10(rms)
                
                # Calculate gain adjustment
                target_level_db = self.config.AUDIO_AGC_TARGET_LEVEL
                gain_db = target_level_db - current_level_db
                
                # Apply compression ratio
                if abs(gain_db) > 0:
                    if gain_db > 0:  # Need to increase gain
                        gain_db = gain_db / self.config.AUDIO_AGC_COMPRESSION_RATIO
                    else:  # Need to decrease gain
                        gain_db = gain_db * self.config.AUDIO_AGC_COMPRESSION_RATIO
                
                # Convert gain back to linear scale
                gain_linear = 10 ** (gain_db / 20)
                
                # Apply gain
                audio_data *= gain_linear
            
            return audio_data
            
        except Exception as e:
            logger.warning(f"AGC failed: {e}")
            return audio_data
    
    def detect_noise_level(self, audio_data: np.ndarray) -> float:
        """Detect noise level in audio data."""
        try:
            if len(audio_data) == 0:
                return -60.0
            
            # Calculate RMS level
            rms = np.sqrt(np.mean(audio_data ** 2))
            
            if rms > 0:
                return 20 * np.log10(rms)
            else:
                return -60.0
                
        except Exception as e:
            logger.warning(f"Noise level detection failed: {e}")
            return -60.0
    
    def should_trigger_noise_handling(self, audio_data: np.ndarray) -> bool:
        """Determine if noise handling should be triggered."""
        noise_level = self.detect_noise_level(audio_data)
        return noise_level > self.config.AUDIO_NOISE_LEVEL_THRESHOLD
    
    def get_adaptive_buffer_size(self, noise_level: float) -> int:
        """Get adaptive buffer size based on noise level."""
        if not self.config.AUDIO_ADAPTIVE_BUFFERING:
            return self.config.AUDIO_BUFFER_SIZE_MS
        
        if noise_level > self.config.AUDIO_NOISE_LEVEL_THRESHOLD:
            # Increase buffer size in noisy environments
            adaptive_size = int(self.config.AUDIO_BUFFER_SIZE_MS * self.config.AUDIO_BUFFER_SCALING_FACTOR)
            return min(adaptive_size, self.config.AUDIO_MAX_NOISE_BUFFER_SIZE_MS)
        else:
            return self.config.AUDIO_BUFFER_SIZE_MS
    
    def update_noise_profile(self, audio_data: np.ndarray):
        """Update noise profile for better noise reduction."""
        try:
            if len(audio_data) > 0:
                stft = librosa.stft(audio_data, n_fft=self.frame_length, hop_length=self.hop_length)
                current_profile = np.mean(np.abs(stft), axis=1, keepdims=True)
                
                if self.noise_profile is None:
                    self.noise_profile = current_profile
                else:
                    # Update noise profile with exponential moving average
                    alpha = 0.1  # Learning rate
                    self.noise_profile = alpha * current_profile + (1 - alpha) * self.noise_profile
                    
        except Exception as e:
            logger.warning(f"Failed to update noise profile: {e}")


class EnhancedVADProcessor:
    """
    Enhanced Voice Activity Detection processor with noise-adaptive settings.
    """
    
    def __init__(self):
        self.config = VoiceSettings
        self.speech_detected = False
        self.speech_start_time = 0
        self.last_speech_time = 0
        self.noise_level_history = []
        
    def process_vad_decision(self, vad_decision: bool, audio_data: np.ndarray, timestamp: float) -> bool:
        """
        Process VAD decision with noise-adaptive logic.
        
        Args:
            vad_decision: Raw VAD decision
            audio_data: Audio data for noise analysis
            timestamp: Current timestamp
            
        Returns:
            Enhanced VAD decision
        """
        try:
            # Update noise level history
            if len(audio_data) > 0:
                noise_level = self._calculate_noise_level(audio_data)
                self.noise_level_history.append(noise_level)
                
                # Keep only recent history
                if len(self.noise_level_history) > 10:
                    self.noise_level_history.pop(0)
            
            # Adjust VAD sensitivity based on noise level
            adjusted_vad = self._adjust_vad_sensitivity(vad_decision, audio_data)
            
            # Apply speech duration constraints
            final_decision = self._apply_speech_constraints(adjusted_vad, timestamp)
            
            return final_decision
            
        except Exception as e:
            logger.error(f"Error in VAD processing: {e}")
            return vad_decision
    
    def _calculate_noise_level(self, audio_data: np.ndarray) -> float:
        """Calculate noise level in audio data."""
        try:
            if len(audio_data) == 0:
                return -60.0
            
            # Calculate RMS level
            rms = np.sqrt(np.mean(audio_data ** 2))
            
            if rms > 0:
                return 20 * np.log10(rms)
            else:
                return -60.0
                
        except Exception as e:
            logger.warning(f"Noise level calculation failed: {e}")
            return -60.0
    
    def _adjust_vad_sensitivity(self, vad_decision: bool, audio_data: np.ndarray) -> bool:
        """Adjust VAD sensitivity based on noise level."""
        try:
            if not self.noise_level_history:
                return vad_decision
            
            avg_noise_level = np.mean(self.noise_level_history)
            
            # In noisy environments, be more conservative with VAD
            if avg_noise_level > self.config.AUDIO_NOISE_LEVEL_THRESHOLD:
                # Require stronger signal for speech detection
                if vad_decision:
                    # Additional check: ensure signal is strong enough
                    signal_strength = np.max(np.abs(audio_data))
                    if signal_strength < 0.3:  # Threshold for strong signal
                        return False
            
            return vad_decision
            
        except Exception as e:
            logger.warning(f"VAD sensitivity adjustment failed: {e}")
            return vad_decision
    
    def _apply_speech_constraints(self, vad_decision: bool, timestamp: float) -> bool:
        """Apply speech duration and timing constraints."""
        try:
            current_time = timestamp
            
            if vad_decision and not self.speech_detected:
                # Speech started
                self.speech_detected = True
                self.speech_start_time = current_time
                self.last_speech_time = current_time
                return True
                
            elif vad_decision and self.speech_detected:
                # Speech continuing
                self.last_speech_time = current_time
                
                # Check maximum speech duration
                speech_duration = current_time - self.speech_start_time
                if speech_duration > self.config.VAD_MAX_SPEECH_DURATION_MS:
                    # Force end of speech
                    self.speech_detected = False
                    return False
                
                return True
                
            elif not vad_decision and self.speech_detected:
                # Check if silence duration exceeds threshold
                silence_duration = current_time - self.last_speech_time
                if silence_duration > self.config.VAD_SILENCE_DURATION_MS:
                    # End of speech
                    self.speech_detected = False
                    return False
                
                # Still in speech (within silence threshold)
                return True
                
            else:
                # No speech detected
                return False
                
        except Exception as e:
            logger.error(f"Speech constraint application failed: {e}")
            return vad_decision
    
    def reset(self):
        """Reset VAD processor state."""
        self.speech_detected = False
        self.speech_start_time = 0
        self.last_speech_time = 0
        self.noise_level_history = []
