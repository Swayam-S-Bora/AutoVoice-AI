import whisper
from app.logger import app_logger
import os
import tempfile
import numpy as np
import wave
import scipy.io.wavfile as wavfile
from scipy import signal
import io

# Load Whisper model
app_logger.info("Loading Whisper model...")
model = whisper.load_model("base")
app_logger.info("Whisper model loaded successfully!")

def validate_and_fix_audio(audio_bytes):
    """
    Validate audio format and fix common issues
    Returns: (fixed_audio_bytes, error_message)
    """
    temp_path = None
    try:
        # Save bytes to temp file
        temp_dir = os.path.join(os.getcwd(), "temp_audio")
        if not os.path.exists(temp_dir):
            os.makedirs(temp_dir)
        
        temp_path = os.path.join(temp_dir, f"validate_{os.getpid()}.wav")
        with open(temp_path, "wb") as f:
            f.write(audio_bytes)
        
        # Try to read with scipy
        sample_rate, audio_data = wavfile.read(temp_path)
        
        app_logger.info(f"Original audio: rate={sample_rate}Hz, shape={audio_data.shape}, dtype={audio_data.dtype}")
        
        # Check if format needs fixing
        needs_fix = False
        issues = []
        
        # Check sample rate
        if sample_rate != 16000:
            needs_fix = True
            issues.append(f"sample rate {sample_rate}Hz (should be 16000Hz)")
        
        # Check channels
        is_stereo = len(audio_data.shape) > 1 and audio_data.shape[1] > 1
        if is_stereo:
            needs_fix = True
            issues.append("stereo (should be mono)")
        
        # Check bit depth
        if audio_data.dtype not in [np.int16, np.float32]:
            needs_fix = True
            issues.append(f"bit depth {audio_data.dtype}")
        
        # Check if audio is too quiet
        if np.max(np.abs(audio_data)) < 100:  # Too quiet
            needs_fix = True
            issues.append("audio too quiet")
        
        if needs_fix:
            app_logger.warning(f"Audio needs fixing: {', '.join(issues)}")
            
            # Fix audio
            # Convert to mono if stereo
            if is_stereo:
                audio_data = np.mean(audio_data, axis=1)
            
            # Convert to int16 if needed
            if audio_data.dtype != np.int16:
                if audio_data.dtype == np.float32:
                    audio_data = (audio_data * 32767).astype(np.int16)
                elif audio_data.dtype == np.int32:
                    audio_data = (audio_data / 65536).astype(np.int16)
            
            # Resample if needed
            if sample_rate != 16000:
                # Simple resampling by interpolation
                target_length = int(len(audio_data) * 16000 / sample_rate)
                audio_data = np.interp(
                    np.linspace(0, len(audio_data), target_length),
                    np.arange(len(audio_data)),
                    audio_data
                ).astype(np.int16)
                sample_rate = 16000
            
            # Normalize volume if too quiet
            if np.max(np.abs(audio_data)) < 10000:
                # Amplify but avoid clipping
                scale = 30000 / (np.max(np.abs(audio_data)) + 1)
                audio_data = (audio_data * scale).astype(np.int16)
            
            # Save fixed audio
            fixed_path = os.path.join(temp_dir, f"fixed_{os.getpid()}.wav")
            wavfile.write(fixed_path, sample_rate, audio_data)
            
            # Read back as bytes
            with open(fixed_path, "rb") as f:
                fixed_bytes = f.read()
            
            # Clean up
            os.unlink(fixed_path)
            
            app_logger.info(f"Audio fixed: now {sample_rate}Hz mono int16")
            return fixed_bytes, None
        else:
            # Audio is already in correct format
            return audio_bytes, None
            
    except Exception as e:
        app_logger.error(f"Audio validation error: {str(e)}")
        return audio_bytes, str(e)
    finally:
        # Clean up temp file
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except:
                pass

async def transcribe_audio(audio_bytes: bytes) -> str:
    """
    Convert audio bytes to text using Whisper
    """
    try:
        # First validate and fix audio format
        fixed_bytes, error = validate_and_fix_audio(audio_bytes)
        
        if error:
            app_logger.warning(f"Using original audio despite error: {error}")
            fixed_bytes = audio_bytes
        
        # Save fixed audio to temp file
        temp_dir = os.path.join(os.getcwd(), "temp_audio")
        temp_path = os.path.join(temp_dir, f"transcribe_{os.getpid()}.wav")
        
        with open(temp_path, "wb") as f:
            f.write(fixed_bytes)
        
        app_logger.info(f"Saved fixed audio: {temp_path}, size: {os.path.getsize(temp_path)} bytes")
        
        # Read for Whisper
        sample_rate, audio_data = wavfile.read(temp_path)
        
        # Convert to float32 for Whisper (normalized to [-1, 1])
        audio_float = audio_data.astype(np.float32) / 32768.0
        
        # Transcribe
        result = model.transcribe(
            audio_float,
            language='en',
            task='transcribe',
            fp16=False,
            verbose=False
        )
        
        text = result["text"].strip()
        app_logger.info(f"Transcription: '{text}'")
        
        # Clean up
        os.unlink(temp_path)
        
        # If transcription is gibberish (contains non-English patterns)
        if text and not any(c.isalpha() for c in text):
            app_logger.warning(f"Gibberish detected: '{text}'")
            return ""
        
        return text.lower()
    
    except Exception as e:
        app_logger.error(f"Transcription error: {str(e)}")
        return ""
    finally:
        # Clean up any remaining temp files
        try:
            temp_path = os.path.join(os.getcwd(), "temp_audio", f"transcribe_{os.getpid()}.wav")
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        except:
            pass