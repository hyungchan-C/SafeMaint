import { useEffect, useState } from "react";

import { getAccessToken, getApiBaseUrl } from "@/lib/api";

export interface DocumentViewerTarget {
  documentId: string;
  documentVersionId: string | null;
  page: number | null;
  title: string;
}

// 채팅 근거로 인용된 문서를 실제로 열어서 읽을 수 있게 해주는 모달. 업로드된 PDF는
// 인증이 필요한 파일이라 <a href>로 바로 열 수 없어서, SecureCandidateImage와 같은
// 방식(fetch + Authorization 헤더 + blob → object URL)으로 받아온 뒤 iframe에 띄운다.
export default function DocumentViewerModal({
  target,
  onClose,
}: {
  target: DocumentViewerTarget;
  onClose: () => void;
}) {
  const [objectUrl, setObjectUrl] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    let createdUrl = "";
    setObjectUrl("");
    setError("");
    const versionParam = target.documentVersionId ? `?version_id=${target.documentVersionId}` : "";
    void fetch(`${getApiBaseUrl()}/api/v1/documents/${target.documentId}/file${versionParam}`, {
      headers: { Authorization: `Bearer ${getAccessToken()}` },
    })
      .then((response) => {
        if (!response.ok) throw new Error("문서를 불러오지 못했습니다.");
        return response.blob();
      })
      .then((blob) => {
        createdUrl = URL.createObjectURL(blob);
        if (active) setObjectUrl(createdUrl);
      })
      .catch(() => {
        if (active) setError("문서를 불러오지 못했습니다. 원문 파일이 없거나 열람 권한이 없을 수 있습니다.");
      });
    return () => {
      active = false;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
    };
  }, [target.documentId, target.documentVersionId]);

  return (
    <div className="document-viewer-backdrop" role="presentation" onClick={onClose}>
      <div
        className="document-viewer-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={target.title}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="document-viewer-header">
          <strong>{target.title}</strong>
          <button type="button" onClick={onClose} aria-label="닫기">닫기</button>
        </div>
        {error ? (
          <p className="error-message">{error}</p>
        ) : objectUrl ? (
          <iframe
            src={target.page ? `${objectUrl}#page=${target.page}` : objectUrl}
            title={target.title}
            className="document-viewer-frame"
          />
        ) : (
          <p className="muted-copy">문서를 불러오는 중...</p>
        )}
      </div>
    </div>
  );
}
