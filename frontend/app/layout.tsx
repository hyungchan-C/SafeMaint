import type { Metadata } from "next";

import "./globals.css";


export const metadata: Metadata = {
  title: "SafeMaint AI",
  description: "제조설비 정비작업 안전관리 지원 대시보드",
};


export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
