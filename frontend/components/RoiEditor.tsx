"use client";

import { useEffect, useState } from "react";

type RoiBounds = { left: number; top: number; right: number; bottom: number };
type Props = { file: File; onCancel: () => void; onConfirm: (file: File) => void };

export default function RoiEditor({ file, onCancel, onConfirm }: Props) {
  const [imageUrl, setImageUrl] = useState("");
  const [bounds, setBounds] = useState<RoiBounds>({ left: 0.05, top: 0.05, right: 0.95, bottom: 0.95 });
  const [error, setError] = useState("");

  useEffect(() => {
    const url = URL.createObjectURL(file);
    setImageUrl(url);
    setBounds({ left: 0.05, top: 0.05, right: 0.95, bottom: 0.95 });
    setError("");
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const setBound = (key: keyof RoiBounds, value: number) =>
    setBounds((current) => ({ ...current, [key]: Math.max(0, Math.min(1, value)) }));

  const createCrop = () => {
    if (!imageUrl) return;
    const image = new Image();
    image.onload = () => {
      const left = Math.round(bounds.left * image.naturalWidth);
      const top = Math.round(bounds.top * image.naturalHeight);
      const right = Math.round(bounds.right * image.naturalWidth);
      const bottom = Math.round(bounds.bottom * image.naturalHeight);
      const width = Math.max(1, right - left);
      const height = Math.max(1, bottom - top);
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const context = canvas.getContext("2d");
      if (!context) {
        setError("이미지 자르기에 실패했습니다.");
        return;
      }
      context.drawImage(image, left, top, width, height, 0, 0, width, height);
      canvas.toBlob((blob) => {
        if (!blob) {
          setError("이미지 변환에 실패했습니다.");
          return;
        }
        const stem = file.name.replace(/\.[^.]+$/, "") || "현장사진";
        onConfirm(new File([blob], `${stem}-roi.jpg`, { type: "image/jpeg" }));
      }, "image/jpeg", 0.92);
    };
    image.onerror = () => setError("이미지를 불러오지 못했습니다.");
    image.src = imageUrl;
  };

  const values: Record<keyof RoiBounds, number> = {
    left: Math.round(bounds.left * 100),
    top: Math.round(bounds.top * 100),
    right: Math.round(bounds.right * 100),
    bottom: Math.round(bounds.bottom * 100),
  };
  const controls: Array<[keyof RoiBounds, string, number, number]> = [
    ["left", "왼쪽 경계", 0, Math.max(0, values.right - 5)],
    ["right", "오른쪽 경계", Math.min(100, values.left + 5), 100],
    ["top", "위쪽 경계", 0, Math.max(0, values.bottom - 5)],
    ["bottom", "아래쪽 경계", Math.min(100, values.top + 5), 100],
  ];

  return (
    <div className="roi-editor-backdrop" role="presentation">
      <section className="roi-editor" role="dialog" aria-modal="true" aria-labelledby="roi-editor-title">
        <div className="roi-editor-heading">
          <div>
            <p className="eyebrow">사진 분석 범위</p>
            <h2 id="roi-editor-title">제품 영역을 선택하세요</h2>
          </div>
          <button type="button" className="roi-editor-close" onClick={onCancel} aria-label="닫기">
            ×
          </button>
        </div>
        <p className="roi-editor-file">{file.name}</p>
        <p className="roi-editor-help">
          배경을 줄이려면 가로·세로 경계를 각각 조절한 뒤 분석을 시작하세요. 원본 사진은 보존됩니다.
        </p>
        {imageUrl && (
          <div className="roi-preview">
            <img src={imageUrl} alt="분석할 현장 사진" />
            <div
              className="roi-window"
              style={{
                left: `${values.left}%`,
                top: `${values.top}%`,
                width: `${values.right - values.left}%`,
                height: `${values.bottom - values.top}%`,
              }}
            />
          </div>
        )}
        <div className="roi-controls">
          {controls.map(([key, label, min, max]) => (
            <label className="roi-range" key={key}>
              <span>
                {label} <strong>{values[key]}%</strong>
              </span>
              <input
                type="range"
                min={min}
                max={max}
                value={values[key]}
                onChange={(event) => setBound(key, Number(event.target.value) / 100)}
              />
            </label>
          ))}
        </div>
        {error && (
          <p className="roi-editor-error" role="alert">
            {error}
          </p>
        )}
        <div className="roi-editor-actions">
          <button type="button" className="secondary-button" onClick={onCancel}>
            취소
          </button>
          <button type="button" className="primary-button" onClick={createCrop} disabled={!imageUrl}>
            이 영역으로 분석
          </button>
        </div>
      </section>
    </div>
  );
}
