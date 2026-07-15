"use client";

import { FormEvent, useState } from "react";
import { getApiBaseUrl } from "@/lib/api";

type SignupForm = { name: string; employee_number: string; password: string; confirm: string; email: string; department: string; job_title: string };
const initialForm: SignupForm = { name: "", employee_number: "", password: "", confirm: "", email: "", department: "", job_title: "" };

export default function SignupPage() {
  const [form, setForm] = useState(initialForm);
  const [agreed, setAgreed] = useState(false);
  const [message, setMessage] = useState("");
  const [isError, setIsError] = useState(false);
  const [isLoading, setIsLoading] = useState(false);

  function update(field: keyof SignupForm, value: string) { setForm((current) => ({ ...current, [field]: value })); }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (form.password !== form.confirm) { setMessage("비밀번호가 일치하지 않습니다."); setIsError(true); return; }
    if (!agreed) { setMessage("개인정보 처리 및 서비스 이용 안내에 동의해 주세요."); setIsError(true); return; }
    setIsLoading(true); setMessage("");
    try {
      const response = await fetch(`${getApiBaseUrl()}/api/v1/auth/register`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: form.name, employee_number: form.employee_number, password: form.password, email: form.email || null, department: form.department || null, job_title: form.job_title || null }),
      });
      const payload = await response.json() as { detail?: string | Array<{ msg: string }> };
      if (!response.ok) { const detail = Array.isArray(payload.detail) ? payload.detail[0]?.msg : payload.detail; throw new Error(detail || "회원가입에 실패했습니다."); }
      setMessage("회원가입이 완료되었습니다. 로그인 화면으로 이동합니다."); setIsError(false);
      window.setTimeout(() => window.location.assign("/"), 900);
    } catch (requestError) { setMessage(requestError instanceof Error ? requestError.message : "백엔드에 연결할 수 없습니다."); setIsError(true); }
    finally { setIsLoading(false); }
  }

  return <main className="auth-shell"><section className="auth-card signup-card">
    <span className="eyebrow">SafeMaint AI 계정</span><h1>회원가입</h1><p>현장 안전관리 서비스에서 사용할 사원 정보를 입력해 주세요.</p>
    <form className="auth-form signup-form" onSubmit={submit}><div className="signup-grid">
      <label>이름 <span className="required-mark">필수</span><input value={form.name} onChange={(e) => update("name", e.target.value)} maxLength={100} autoComplete="name" required /></label>
      <label>사원번호(ID) <span className="required-mark">필수 · 최대 30자</span><input value={form.employee_number} onChange={(e) => update("employee_number", e.target.value)} maxLength={30} autoComplete="username" required /></label>
      <label>비밀번호 <span className="required-mark">필수 · 12자 이상</span><input type="password" value={form.password} onChange={(e) => update("password", e.target.value)} minLength={12} maxLength={128} autoComplete="new-password" required /></label>
      <label>비밀번호 확인 <span className="required-mark">필수</span><input type="password" value={form.confirm} onChange={(e) => update("confirm", e.target.value)} minLength={12} maxLength={128} autoComplete="new-password" required /></label>
      <label>이메일 <span className="optional-mark">선택</span><input type="email" value={form.email} onChange={(e) => update("email", e.target.value)} maxLength={255} autoComplete="email" placeholder="employee@example.com" /></label>
      <label>소속 부서 <span className="optional-mark">선택</span><input value={form.department} onChange={(e) => update("department", e.target.value)} maxLength={100} autoComplete="organization" /></label>
      <label>직책 <span className="optional-mark">선택</span><input value={form.job_title} onChange={(e) => update("job_title", e.target.value)} maxLength={100} autoComplete="organization-title" /></label>
    </div><label className="checkbox-line"><input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} /> 개인정보 처리 및 서비스 이용 안내에 동의합니다.</label>
    {message && <p className={isError ? "error-message" : "success-message"} role="status">{message}</p>}
    <button className="primary-button" disabled={isLoading} type="submit">{isLoading ? "가입 처리 중..." : "회원가입 완료"}</button></form>
    <a className="secondary-button auth-link" href="/">로그인 화면으로 돌아가기</a>
  </section></main>;
}
