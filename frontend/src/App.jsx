import React, { useEffect, useState } from "react";
import NewBrief from "./components/NewBrief.jsx";
import BriefPage from "./components/BriefPage.jsx";
import SharePage from "./components/SharePage.jsx";

// Tiny router: / (new brief), /b/:id (working brief), /s/:token (shared booking brief)
export function navigate(path) {
  window.history.pushState({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export default function App() {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const on = () => {
      setPath(window.location.pathname);
      window.scrollTo(0, 0);
    };
    window.addEventListener("popstate", on);
    return () => window.removeEventListener("popstate", on);
  }, []);

  const share = path.match(/^\/s\/([\w-]+)/);
  if (share) return <SharePage token={share[1]} />;
  const brief = path.match(/^\/b\/([\w-]+)/);
  return (
    <div className="shell">
      <header className="topbar">
        <a href="/" className="wordmark" onClick={(e) => { e.preventDefault(); navigate("/"); }}>
          Opening Act
        </a>
        <span className="topbar-note">Support-act research for independent venues, powered by Qloo taste data</span>
      </header>
      {brief ? <BriefPage key={brief[1]} id={brief[1]} /> : <NewBrief />}
    </div>
  );
}
