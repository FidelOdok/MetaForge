import { useEffect, useState, type ImgHTMLAttributes, type MouseEvent, type ReactNode } from 'react';
import apiClient from '../../api/client';

/**
 * An `<img>` cannot send the gateway's bearer token, so on a gateway that
 * requires one (any hosted environment) an image pointed straight at an API
 * route gets 401 and the browser shows only its alt text. Locally, with auth
 * off, the bug never shows. These fetch the file through the API client,
 * which adds the token, and hand the browser a local object URL instead.
 */
export function useAuthedObjectUrl(src: string | null | undefined): { url: string | null; error: string | null } {
  const [state, setState] = useState<{ src: string | null; url: string | null; error: string | null }>({
    src: null,
    url: null,
    error: null,
  });
  useEffect(() => {
    if (!src) return undefined;
    let cancelled = false;
    let made: string | null = null;
    apiClient
      .get<Blob>(src, { responseType: 'blob' })
      .then(({ data }) => {
        if (cancelled) return;
        made = URL.createObjectURL(data);
        setState({ src, url: made, error: null });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const status = (err as { response?: { status?: number } })?.response?.status;
        setState({ src, url: null, error: status ? `could not load it (HTTP ${status})` : 'could not load it' });
      });
    return () => {
      cancelled = true;
      if (made) URL.revokeObjectURL(made);
    };
  }, [src]);
  // A result for an earlier src never shows for the current one.
  return state.src === src ? { url: state.url, error: state.error } : { url: null, error: null };
}

type AuthedImgProps = Omit<ImgHTMLAttributes<HTMLImageElement>, 'src'> & { src: string };

/** An image from a gateway route, loaded with the person's token. */
export function AuthedImg({ src, alt, ...rest }: AuthedImgProps) {
  const { url, error } = useAuthedObjectUrl(src);
  if (error) {
    return (
      <span className="authed-img-error" role="img" aria-label={alt}>
        {alt}: {error}
      </span>
    );
  }
  if (!url) return <span className="authed-img-loading" aria-busy="true" />;
  return <img src={url} alt={alt} {...rest} />;
}

interface AuthedFileLinkProps {
  src: string;
  /** The file name the browser saves it as; omit to open it in a new tab. */
  download?: string;
  children: ReactNode;
  className?: string;
  style?: React.CSSProperties;
  title?: string;
}

/** A link to a gateway file that fetches it with the token when clicked. */
export function AuthedFileLink({ src, download, children, ...rest }: AuthedFileLinkProps) {
  const [error, setError] = useState<string | null>(null);
  const open = async (e: MouseEvent<HTMLAnchorElement>) => {
    e.preventDefault();
    setError(null);
    try {
      const { data } = await apiClient.get<Blob>(src, { responseType: 'blob' });
      const url = URL.createObjectURL(data);
      const a = document.createElement('a');
      a.href = url;
      if (download !== undefined) a.download = download;
      else a.target = '_blank';
      a.rel = 'noreferrer';
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (err: unknown) {
      const status = (err as { response?: { status?: number } })?.response?.status;
      setError(status ? `HTTP ${status}` : 'failed');
    }
  };
  return (
    <a href={src} onClick={open} {...rest}>
      {children}
      {error && <span className="authed-img-error"> (could not open it: {error})</span>}
    </a>
  );
}
