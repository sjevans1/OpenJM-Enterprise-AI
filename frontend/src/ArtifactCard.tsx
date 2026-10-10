import { useState } from 'react'
import { Download, FileText, Trash2 } from 'lucide-react'
import { api, type ChatArtifact } from './api'

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

const FORMAT_LABEL: Record<ChatArtifact['artifact_format'], string> = {
  html: 'HTML',
  markdown: 'Markdown',
  text: 'Text',
  csv: 'CSV',
}

/**
 * A downloadable card for a Chat artifact.
 *
 * It renders ONLY server-issued metadata (id, filename, MIME, size) and a
 * download action; it never renders file content, base64 or a storage path. The
 * object stays labelled a Chat artifact — not an approved, authoritative or
 * governed report — regardless of whether it is evidence-backed.
 */
export default function ArtifactCard({
  artifact,
  onDeleted,
}: {
  artifact: ChatArtifact
  onDeleted?: (id: string) => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = async (action: () => Promise<unknown>) => {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      await action()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Artifact action failed')
    } finally {
      setBusy(false)
    }
  }

  const remove = () =>
    run(async () => {
      await api.deleteArtifact(artifact.id)
      onDeleted?.(artifact.id)
    })

  return (
    <div className="artifact-card" data-artifact-id={artifact.id}>
      <div className="artifact-card-head">
        <div className="artifact-icon" aria-hidden="true">
          <FileText size={16} />
        </div>
        <div className="artifact-identity">
          <strong className="artifact-filename">{artifact.filename}</strong>
          <span className="artifact-meta">
            {FORMAT_LABEL[artifact.artifact_format] || artifact.artifact_format} ·{' '}
            {artifact.mime_type} · {formatBytes(artifact.size_bytes)}
          </span>
        </div>
      </div>

      {/* Metadata only: a Chat artifact is never an approved/authoritative report. */}
      <div className="artifact-disclaimer">
        Chat artifact · not a governed report{artifact.is_evidence_backed ? ' · evidence cited in provenance' : ''}
      </div>

      <div className="artifact-actions">
        <button
          type="button"
          className="primary-action artifact-download"
          disabled={busy}
          aria-label={`Download ${artifact.filename}`}
          onClick={() => run(() => api.downloadArtifact(artifact.id, artifact.filename))}
        >
          <Download size={14} />
          Download
        </button>
        <button
          type="button"
          className="secondary-action artifact-render-pdf"
          disabled={busy}
          aria-label={`Download PDF of ${artifact.filename}`}
          onClick={() =>
            run(() =>
              api.downloadArtifactRender(artifact.id, 'pdf', artifact.filename.replace(/\.[^.]+$/, '.pdf')),
            )
          }
        >
          PDF
        </button>
        <button
          type="button"
          className="secondary-action artifact-render-docx"
          disabled={busy}
          aria-label={`Download DOCX of ${artifact.filename}`}
          onClick={() =>
            run(() =>
              api.downloadArtifactRender(artifact.id, 'docx', artifact.filename.replace(/\.[^.]+$/, '.docx')),
            )
          }
        >
          DOCX
        </button>
        <button
          type="button"
          className="icon-button danger artifact-remove"
          disabled={busy}
          aria-label={`Remove ${artifact.filename}`}
          title="Remove artifact"
          onClick={remove}
        >
          <Trash2 size={14} />
        </button>
      </div>

      {error && (
        <div className="artifact-error" role="alert">
          {error}
        </div>
      )}
    </div>
  )
}
