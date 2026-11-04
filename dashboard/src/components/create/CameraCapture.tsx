import { useCallback, useEffect, useRef, useState } from 'react';
import { Camera, CameraOff } from 'lucide-react';

type CameraState = 'off' | 'starting' | 'live' | 'blocked' | 'unavailable';

interface CameraCaptureProps {
  /** A snapshot, as a JPEG file ready to upload. */
  onSnapshot: (file: File) => void;
  disabled?: boolean;
}

/**
 * Optional live camera for inspection: pick a camera, see it, take
 * snapshots. Nothing is recorded continuously; only the snapshots you take
 * are kept, as files in this creation. A picture is an observation, never a
 * calibrated measurement.
 */
export function CameraCapture({ onSnapshot, disabled }: CameraCaptureProps) {
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const [state, setState] = useState<CameraState>('off');
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState('');
  const [quality, setQuality] = useState('');

  const stop = useCallback(() => {
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
    setState('off');
  }, []);

  // Leaving the tab or the view ends the camera session; it is started again on purpose.
  useEffect(() => stop, [stop]);

  const start = async (id = deviceId) => {
    if (!navigator.mediaDevices?.getUserMedia) {
      setState('unavailable');
      return;
    }
    setState('starting');
    try {
      stream.current?.getTracks().forEach((t) => t.stop());
      const s = await navigator.mediaDevices.getUserMedia({
        video: id ? { deviceId: { exact: id } } : { facingMode: 'environment' },
        audio: false,
      });
      stream.current = s;
      if (video.current) {
        video.current.srcObject = s;
        await video.current.play().catch(() => undefined);
      }
      const settings = s.getVideoTracks()[0]?.getSettings();
      setQuality(settings?.width ? `${settings.width}×${settings.height}` : '');
      setDevices((await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === 'videoinput'));
      setState('live');
    } catch (error) {
      setState((error as DOMException)?.name === 'NotAllowedError' ? 'blocked' : 'unavailable');
    }
  };

  const snap = () => {
    const v = video.current;
    if (!v || !v.videoWidth) return;
    const canvas = document.createElement('canvas');
    canvas.width = v.videoWidth;
    canvas.height = v.videoHeight;
    canvas.getContext('2d')?.drawImage(v, 0, 0);
    canvas.toBlob(
      (blob) => {
        if (blob) onSnapshot(new File([blob], `inspection-${new Date().toISOString().replace(/[:.]/g, '-')}.jpg`, { type: 'image/jpeg' }));
      },
      'image/jpeg',
      0.92,
    );
  };

  return (
    <div className="cv-camera">
      {state === 'live' || state === 'starting' ? (
        <>
          <video ref={video} className="cv-camera-video" muted playsInline aria-label="Live camera" />
          <p className="cv-fineprint">
            Hold the part square to the camera, in even light, filling most of the frame.
            {quality ? ` Camera: ${quality}.` : ''}
          </p>
          <div className="cv-actions">
            <button type="button" className="cv-btn cv-btn-primary" onClick={snap} disabled={disabled || state !== 'live'}>
              <Camera size={14} /> Take snapshot
            </button>
            {devices.length > 1 && (
              <select
                aria-label="Camera"
                className="cv-input"
                value={deviceId}
                onChange={(e) => {
                  setDeviceId(e.target.value);
                  void start(e.target.value);
                }}
              >
                <option value="">Default camera</option>
                {devices.map((d, i) => (
                  <option key={d.deviceId} value={d.deviceId}>
                    {d.label || `Camera ${i + 1}`}
                  </option>
                ))}
              </select>
            )}
            <button type="button" className="cv-btn" onClick={stop}>
              <CameraOff size={14} /> Stop camera
            </button>
          </div>
        </>
      ) : (
        <div className="cv-actions">
          <button type="button" className="cv-btn" onClick={() => void start()} disabled={disabled}>
            <Camera size={14} /> Use a camera
          </button>
          <span className="cv-muted">
            {state === 'blocked'
              ? 'Camera access was refused. Allow it in the browser, or upload photos instead.'
              : state === 'unavailable'
                ? 'No camera is available here. Upload photos instead.'
                : 'Optional. You can upload photos instead.'}
          </span>
        </div>
      )}
    </div>
  );
}
