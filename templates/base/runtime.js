/* aside-shorts 런타임 — 모든 모션은 TL(멈춰 둔 GSAP 타임라인) 하나에 시각을 박아 넣는다.
 * 렌더러가 프레임마다 __seek(t) 로 그 시각을 찍는다 → 실시간 재생과 무관하게 언제나 같은 그림(결정론).
 *
 * 장면 스크립트에서 쓰는 것:
 *   SHORT             {duration, hook:{line1,line2}, cues:[{i,text,keywords,start,end}], images:{img1:{src,role,subject}}}
 *   TL                gsap.timeline({paused:true}) — 여기에만 트윈을 넣는다. 위치(초)를 꼭 준다.
 *   at(i, off=0)      i번 문장이 읽히기 시작하는 시각(+off초)
 *   end(i, off=0)     i번 문장이 끝나는 시각(+off초)
 *   IMG(key)          그림 경로 (img1 …)
 *   rand(seed)        0~1 결정론 난수
 */
(function () {
  window.__errors = [];
  window.addEventListener("error", function (e) { window.__errors.push(String(e.message || e)); });
  if (!window.gsap) { window.__errors.push("gsap 이 없습니다"); return; }
  var S = window.SHORT;
  var TL = gsap.timeline({ paused: true, defaults: { ease: "power3.out", duration: 0.6 } });
  window.TL = TL;
  window.at = function (i, off) { var c = S.cues[Math.max(0, Math.min(S.cues.length - 1, i))]; return Math.max(0, c.start + (off || 0)); };
  window.end = function (i, off) { var c = S.cues[Math.max(0, Math.min(S.cues.length - 1, i))]; return Math.max(0, c.end + (off || 0)); };
  window.IMG = function (k) { var m = S.images[k]; if (!m) { window.__errors.push("없는 그림 키: " + k); return ""; } return m.src; };
  window.rand = function (seed) { var x = Math.sin((seed + 1) * 9301.7) * 49297.3; return x - Math.floor(x); };

  // <img data-img="img1"> → src 채움, 다른 요소의 data-img → 배경 그림
  Array.prototype.forEach.call(document.querySelectorAll("[data-img]"), function (el) {
    var src = window.IMG(el.getAttribute("data-img"));
    if (el.tagName === "IMG") el.setAttribute("src", src);
    else el.style.backgroundImage = 'url("' + src + '")';
  });

  // 상단 후크 — 처음에 한 번 들어와서 끝까지 남는다
  var hook = document.getElementById("hook");
  hook.querySelector(".l1").textContent = (S.hook && S.hook.line1) || "";
  hook.querySelector(".l2").textContent = (S.hook && S.hook.line2) || "";

  // 하단 자막 — 문장마다 하나. 키워드는 강조색
  var esc = function (s) { return String(s).replace(/[&<>]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]; }); };
  var caps = document.getElementById("captions");
  S.cues.forEach(function (c) {
    var html = esc(c.text);
    (c.keywords || []).forEach(function (k) {
      if (!k) return;
      var e = esc(k);
      html = html.split(e).join('<b class="kw">' + e + "</b>");
    });
    var el = document.createElement("div");
    el.className = "cap";
    el.innerHTML = "<span>" + html + "</span>";
    caps.appendChild(el);
    c._el = el;
  });

  window.__finish = function () {
    try {
      TL.fromTo(hook, { yPercent: -110, opacity: 0 }, { yPercent: 0, opacity: 1, duration: 0.5, ease: "back.out(1.6)" }, 0.05);
      S.cues.forEach(function (c, i) {
        var next = S.cues[i + 1];
        var off = next ? next.start : S.duration;
        TL.fromTo(c._el, { opacity: 0, y: 24 }, { opacity: 1, y: 0, duration: 0.18, ease: "power2.out" }, c.start);
        TL.to(c._el, { opacity: 0, duration: 0.1, ease: "none" }, Math.max(c.start + 0.2, off - 0.08));
      });
      TL.set({}, {}, S.duration);          // 타임라인 길이 = 음성 길이
    } catch (e) { window.__errors.push("finish: " + (e && e.stack || e)); }
    var imgs = Array.prototype.slice.call(document.images);
    Promise.all([document.fonts.ready].concat(imgs.map(function (im) {
      return im.decode ? im.decode().catch(function () { window.__errors.push("그림을 못 읽음: " + im.getAttribute("src")); }) : null;
    }))).then(function () { TL.seek(0, false); window.__ready = true; });
  };

  window.__seek = function (t) {
    TL.seek(t, false);
    if (document.getAnimations) document.getAnimations().forEach(function (a) { a.pause(); a.currentTime = t * 1000; });
  };
  // 브라우저에서 그냥 열었을 때(미리보기) — 실시간 재생 + 음성
  window.__play = function () {
    var a = document.getElementById("__audio");
    TL.seek(0).play();
    if (a) { a.currentTime = 0; a.play(); }
  };
})();
