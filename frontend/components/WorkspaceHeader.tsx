"use client";

import InterfaceIcon from "@/components/InterfaceIcon";

type FontSize = "small" | "medium" | "large";

type Props = {
  displayName: string;
  locationStatus: string;
  gpsSource: "real" | "default";
  isRecording: boolean;
  isTranscribing: boolean;
  isSpeaking: boolean;
  volume: number;
  fontSize: FontSize;
  autoSpeak: boolean;
  onVoiceInput: () => void;
  onSpeakGuidance: () => void;
  onHistory: () => void;
  onAssessments: () => void;
  onVolumeChange: (value: number) => void;
  onFontSizeChange: (value: FontSize) => void;
  onAutoSpeakChange: (value: boolean) => void;
  onClearConversation: () => void;
  onClearManuals: () => void;
  onLogout: () => void;
};

export default function WorkspaceHeader({
  displayName,
  locationStatus,
  gpsSource,
  isRecording,
  isTranscribing,
  isSpeaking,
  volume,
  fontSize,
  autoSpeak,
  onVoiceInput,
  onSpeakGuidance,
  onHistory,
  onAssessments,
  onVolumeChange,
  onFontSizeChange,
  onAutoSpeakChange,
  onClearConversation,
  onClearManuals,
  onLogout,
}: Props) {
  return (
    <header className="workspace-header">
      <div className="workspace-brand" aria-label="SafeMaint AI">
        <span className="workspace-brand-mark">SM</span>
        <span><strong>SafeMaint AI</strong><small>현장 안전 작업 지원</small></span>
      </div>

      <div className="workspace-header-status">
        <span className="location-chip"><InterfaceIcon name="location" />{locationStatus}<small>{gpsSource === "real" ? "실제 위치" : "테스트 위치"}</small></span>
        <span className="user-chip"><small>현재 사용자</small><strong>{displayName}</strong></span>
      </div>

      <nav className="workspace-header-actions" aria-label="빠른 기능">
        <button type="button" className={isRecording ? "header-action active" : "header-action"} onClick={onVoiceInput} disabled={isTranscribing}>
          <InterfaceIcon name={isRecording ? "stop" : "microphone"} />
          <span>{isRecording ? "녹음 중지" : isTranscribing ? "인식 중" : "음성 입력"}</span>
        </button>
        <button type="button" className={isSpeaking ? "header-action active" : "header-action"} onClick={onSpeakGuidance}>
          <InterfaceIcon name={isSpeaking ? "stop" : "speaker"} />
          <span>{isSpeaking ? "음성 중지" : "음성 안내"}</span>
        </button>
        <button type="button" className="header-action" onClick={onHistory}>
          <InterfaceIcon name="history" /><span>기록</span>
        </button>
        <button type="button" className="header-action" onClick={onAssessments}>
          <InterfaceIcon name="document" /><span>체크리스트</span>
        </button>
        <details className="settings-control">
          <summary className="header-action" aria-label="환경 설정"><InterfaceIcon name="settings" /><span>설정</span></summary>
          <aside className="settings-popover">
            <div className="settings-heading"><strong>화면·음성 설정</strong><small>이 브라우저에 저장됩니다.</small></div>
            <label>안내 음량 <strong>{volume}</strong><input type="range" min="0" max="100" value={volume} onChange={(event) => onVolumeChange(Number(event.target.value))} /></label>
            <label>글자 크기<select value={fontSize} onChange={(event) => onFontSizeChange(event.target.value as FontSize)}><option value="small">작게</option><option value="medium">보통</option><option value="large">크게</option></select></label>
            <label className="settings-switch"><span>답변 자동 음성 출력</span><input type="checkbox" checked={autoSpeak} onChange={(event) => onAutoSpeakChange(event.target.checked)} /></label>
            <div className="settings-divider" />
            <button type="button" onClick={onClearConversation}>대화 초기화</button>
            <button type="button" onClick={onClearManuals}>매뉴얼 선택 초기화</button>
            <button type="button" className="logout-button" onClick={onLogout}>로그아웃</button>
          </aside>
        </details>
      </nav>
    </header>
  );
}
