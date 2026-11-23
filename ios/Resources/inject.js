// inject.js —— 每次页面加载完成后自动注入（WKUserScript, atDocumentEnd）
// 改完要重新构建一次 app 才会生效。
(() => {
  if (window.__courseShell) return;
  window.__courseShell = { version: 1, at: Date.now() };
  console.log('[shell] injected on', location.href);

  // 等某个节点出现再动手，适合 SPA 渲染慢的页面
  const waitFor = (selector, timeout = 15000) =>
    new Promise((resolve) => {
      const hit = document.querySelector(selector);
      if (hit) return resolve(hit);

      const observer = new MutationObserver(() => {
        const node = document.querySelector(selector);
        if (node) {
          clearTimeout(timer);
          observer.disconnect();
          resolve(node);
        }
      });
      const timer = setTimeout(() => {
        observer.disconnect();
        resolve(null);
      }, timeout);
      observer.observe(document.documentElement, { childList: true, subtree: true });
    });

  window.__courseShellWaitFor = waitFor;

  // 你的逻辑写在这下面，例如：
  //
  // waitFor('.video-area').then((node) => {
  //   if (!node) return;
  //   console.log('[shell] 目标节点到了', node);
  // });
})();
