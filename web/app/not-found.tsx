import Link from "next/link";

export default function NotFound() {
  return (
    <div style={{ padding: "3rem 0", textAlign: "center" }}>
      <h1 className="as-page-title">Page not found · 页面不存在</h1>
      <p className="as-page-sub">
        The page you asked for does not exist.{" "}
        <span lang="zh-Hans">您访问的页面不存在。</span>
      </p>
      <p>
        <Link href="/feed">Go to the signal feed · 返回信号流</Link>
      </p>
    </div>
  );
}
