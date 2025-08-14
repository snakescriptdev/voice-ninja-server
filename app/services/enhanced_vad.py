import numpy as np
from pipecat.audio.vad.silero import SileroVADAnalyzer
from typing import Optional, Tuple
import logging
from app.services.audio_enhancement import EnhancedVADProcessor, AudioEnhancementProcessor
from app.core.config import VoiceSettings
import librosa

logger = logging.getLogger(__name__)

class EnhancedSileroVADAnalyzer(SileroVADAnalyzer):
    """
    Enhanced VAD analyzer that combines Silero VAD with noise filtering
    and adaptive processing for noisy environments.
    """
    
    def __init__(self):
        # Call parent constructor first
        super().__init__()
        self.enhanced_vad = EnhancedVADProcessor()
        self.audio_enhancer = AudioEnhancementProcessor()
        self.config = VoiceSettings
        
        # VAD state tracking
        self.last_vad_decision = False
        self.consecutive_speech_frames = 0
        self.consecutive_silence_frames = 0
        self.min_speech_frames = max(1, int(self.config.VAD_MIN_SPEECH_DURATION_MS / 100))  # Convert to frame count
        self.min_silence_frames = max(1, int(self.config.VAD_SILENCE_DURATION_MS / 100))   # Convert to frame count
        
        # Noise adaptation
        self.noise_level_history = []
        self.adaptive_threshold = 0.5
        
    def analyze(self, audio: np.ndarray, sample_rate: int) -> Tuple[bool, float]:
        """
        Analyze audio for voice activity with enhanced noise filtering.
        
        Args:
            audio: Input audio data
            sample_rate: Audio sample rate
            
        Returns:
            Tuple of (is_speech, confidence)
        """
        try:
            if len(audio) == 0:
                return False, 0.0
            
            # Get raw VAD decision from Silero using parent class
            raw_vad_decision, confidence = super().analyze(audio, sample_rate)
            
            # Apply audio enhancement for noise reduction
            enhanced_audio = self.audio_enhancer.process_audio_frame(audio, is_user_audio=True)
            
            # Update noise profile
            self.audio_enhancer.update_noise_profile(enhanced_audio)
            
            # Detect noise level
            noise_level = self.audio_enhancer.detect_noise_level(enhanced_audio)
            self._update_noise_history(noise_level)
            
            # Apply adaptive threshold based on noise level
            adaptive_decision = self._apply_adaptive_threshold(raw_vad_decision, confidence, noise_level)
            
            # Apply enhanced VAD processing with noise adaptation
            final_decision = self._apply_enhanced_vad_logic(adaptive_decision, enhanced_audio, confidence)
            
            # Update state
            self._update_vad_state(final_decision)
            
            # Update last decision
            self.last_vad_decision = final_decision
            
            return final_decision, confidence
            
        except Exception as e:
            logger.error(f"Error in enhanced VAD analysis: {e}")
            # Fallback to raw VAD decision using parent class
            return super().analyze(audio, sample_rate)
    
    def _update_noise_history(self, noise_level: float):
        """Update noise level history for adaptive processing."""
        self.noise_level_history.append(noise_level)
        
        # Keep only recent history (last 20 frames)
        if len(self.noise_level_history) > 20:
            self.noise_level_history.pop(0)
    
    def _apply_adaptive_threshold(self, raw_vad: bool, confidence: float, noise_level: float) -> bool:
        """Apply adaptive threshold based on noise level."""
        try:
            if not self.noise_level_history:
                return raw_vad
            
            avg_noise_level = np.mean(self.noise_level_history)
            
            # Adjust threshold based on noise level
            if avg_noise_level > self.config.AUDIO_NOISE_LEVEL_THRESHOLD:
                # In noisy environment, require higher confidence
                if confidence < 0.7:  # Higher threshold for noisy environments
                    return False
                
                # Additional check: ensure signal is strong enough
                if confidence < 0.8 and noise_level > -20:  # Very noisy
                    return False
            
            # Apply VAD sensitivity adjustment
            if self.config.VAD_SENSITIVITY < 0.5:
                # More sensitive: lower threshold
                adjusted_confidence = confidence * (1.0 + (0.5 - self.config.VAD_SENSITIVITY))
            else:
                # Less sensitive: higher threshold
                adjusted_confidence = confidence * self.config.VAD_SENSITIVITY
            
            # Apply adaptive threshold
            threshold = self.adaptive_threshold
            if avg_noise_level > self.config.AUDIO_NOISE_LEVEL_THRESHOLD:
                threshold = min(0.8, threshold + 0.2)  # Higher threshold in noise
            else:
                threshold = max(0.3, threshold - 0.1)  # Lower threshold in quiet
            
            return adjusted_confidence > threshold
            
        except Exception as e:
            logger.warning(f"Adaptive threshold application failed: {e}")
            return raw_vad
    
    def _apply_enhanced_vad_logic(self, vad_decision: bool, audio_data: np.ndarray, confidence: float) -> bool:
        """Apply enhanced VAD logic with noise adaptation."""
        try:
            # Get current timestamp (simplified - in real implementation, use actual timestamp)
            import time
            current_time = time.time() * 1000  # Convert to milliseconds
            
            # Process with enhanced VAD
            enhanced_decision = self.enhanced_vad.process_vad_decision(
                vad_decision, audio_data, current_time
            )
            
            # Additional noise-based filtering
            if enhanced_decision and len(audio_data) > 0:
                # Check if this is likely background noise vs. actual speech
                if self._is_likely_background_noise(audio_data, confidence):
                    return False
            
            return enhanced_decision
            
        except Exception as e:
            logger.warning(f"Enhanced VAD logic failed: {e}")
            return vad_decision
    
    def _is_likely_background_noise(self, audio_data: np.ndarray, confidence: float) -> bool:
        """Determine if audio is likely background noise."""
        try:
            if len(audio_data) == 0:
                return True
            
            # Check signal characteristics
            rms = np.sqrt(np.mean(audio_data ** 2))
            peak = np.max(np.abs(audio_data))
            
            # Low RMS and low peak with low confidence suggests background noise
            if rms < 0.05 and peak < 0.1 and confidence < 0.6:
                return True
            
            # Check for uniform noise patterns (background noise is often uniform)
            if len(audio_data) > 100:
                # Calculate variance - low variance suggests uniform noise
                variance = np.var(audio_data)
                if variance < 0.001:  # Very low variance
                    return True
            
            return False
            
        except Exception as e:
            logger.warning(f"Background noise detection failed: {e}")
            return False
    
    def _update_vad_state(self, vad_decision: bool):
        """Update VAD state tracking."""
        if vad_decision:
            self.consecutive_speech_frames += 1
            self.consecutive_silence_frames = 0
        else:
            self.consecutive_silence_frames += 1
            self.consecutive_speech_frames = 0
    
    def reset(self):
        """Reset VAD analyzer state."""
        self.consecutive_speech_frames = 0
        self.consecutive_silence_frames = 0
        self.noise_level_history = []
        self.adaptive_threshold = 0.5
        self.last_vad_decision = False
        self.enhanced_vad.reset()
        self.audio_enhancer.noise_profile = None
    
    # Required VAD interface methods for pipecat compatibility
    def is_speech(self, audio: np.ndarray, sample_rate: int) -> bool:
        """Compatibility method for pipecat VAD interface."""
        is_speech, _ = self.analyze(audio, sample_rate)
        return is_speech
    
    def get_confidence(self, audio: np.ndarray, sample_rate: int) -> float:
        """Compatibility method for pipecat VAD interface."""
        _, confidence = self.analyze(audio, sample_rate)
        return confidence
    
    def get_noise_level(self) -> float:
        """Get current noise level estimate."""
        if self.noise_level_history:
            return np.mean(self.noise_level_history[-5:])  # Average of last 5 frames
        return -60.0
    
    def is_noisy_environment(self) -> bool:
        """Check if current environment is noisy."""
        noise_level = self.get_noise_level()
        return noise_level > self.config.AUDIO_NOISE_LEVEL_THRESHOLD
    
    def get_adaptive_buffer_size(self) -> int:
        """Get adaptive buffer size based on current noise level."""
        return self.audio_enhancer.get_adaptive_buffer_size(self.get_noise_level())


class NoiseAdaptiveAudioProcessor:
    """
    Audio processor that adapts to noise levels for optimal voice quality.
    """
    
    def __init__(self):
        self.config = VoiceSettings
        self.audio_enhancer = AudioEnhancementProcessor()
        self.vad_analyzer = EnhancedSileroVADAnalyzer()
        
    def process_audio(self, audio_data: np.ndarray, is_user_audio: bool = True) -> np.ndarray:
        """
        Process audio with noise-adaptive enhancement.
        
        Args:
            audio_data: Input audio data
            is_user_audio: Whether this is user input audio
            
        Returns:
            Enhanced audio data
        """
        try:
            if len(audio_data) == 0:
                return audio_data
            
            # Apply audio enhancement
            enhanced_audio = self.audio_enhancer.process_audio_frame(audio_data, is_user_audio)
            
            # For user audio, apply additional noise filtering if in noisy environment
            if is_user_audio and self.vad_analyzer.is_noisy_environment():
                # Apply stronger noise reduction in noisy environments
                enhanced_audio = self._apply_stronger_noise_reduction(enhanced_audio)
            
            return enhanced_audio
            
        except Exception as e:
            logger.error(f"Error in noise-adaptive audio processing: {e}")
            return audio_data
    
    def _apply_stronger_noise_reduction(self, audio_data: np.ndarray) -> np.ndarray:
        """Apply stronger noise reduction for noisy environments."""
        try:
            # Apply additional spectral subtraction
            if len(audio_data) > 0:
                # Simple spectral subtraction
                stft = librosa.stft(audio_data, n_fft=512, hop_length=128)
                
                # Estimate noise floor
                noise_floor = np.percentile(np.abs(stft), 10, axis=1, keepdims=True)
                
                # Apply spectral subtraction
                stft_enhanced = stft - noise_floor * 1.5
                stft_enhanced = np.maximum(stft_enhanced, 0)  # Ensure non-negative
                
                # Convert back to time domain
                enhanced_audio = librosa.istft(stft_enhanced, hop_length=128)
                
                return enhanced_audio
            
            return audio_data
            
        except Exception as e:
            logger.warning(f"Stronger noise reduction failed: {e}")
            return audio_data
    
    def get_optimal_buffer_size(self) -> int:
        """Get optimal buffer size based on current noise level."""
        return self.vad_analyzer.get_adaptive_buffer_size()
    
    def get_noise_level(self) -> float:
        """Get current noise level estimate."""
        return self.vad_analyzer.get_noise_level()
    
    def reset(self):
        """Reset processor state."""
        self.vad_analyzer.reset()
        self.audio_enhancer.noise_profile = None
