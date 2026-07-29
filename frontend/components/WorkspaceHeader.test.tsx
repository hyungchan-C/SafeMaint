import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import WorkspaceHeader from "@/components/WorkspaceHeader";

afterEach(cleanup);

describe("WorkspaceHeader", () => {
  it("places the approval notification immediately before settings", () => {
    const { container } = render(
      <WorkspaceHeader
        displayName="관리자"
        locationStatus="위치 확인"
        gpsSource="default"
        isRecording={false}
        isTranscribing={false}
        isSpeaking={false}
        volume={70}
        fontSize="medium"
        autoSpeak={false}
        onVoiceInput={vi.fn()}
        onSpeakGuidance={vi.fn()}
        onHistory={vi.fn()}
        onAssessments={vi.fn()}
        onVolumeChange={vi.fn()}
        onFontSizeChange={vi.fn()}
        onAutoSpeakChange={vi.fn()}
        onClearConversation={vi.fn()}
        onClearManuals={vi.fn()}
        onLogout={vi.fn()}
        notificationCenter={<button type="button" aria-label="문서 승인 알림">알림</button>}
      />,
    );

    const notificationButton = screen.getByRole("button", { name: "문서 승인 알림" });
    const settingsButton = container.querySelector<HTMLElement>(
      'summary[aria-label="환경 설정"]',
    );
    expect(settingsButton).not.toBeNull();
    expect(
      notificationButton.compareDocumentPosition(settingsButton!)
      & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});
