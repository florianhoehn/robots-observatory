<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Страница не найдена. Ошибка 404</title>
<meta name="description" content="Страница не найдена. Вернитесь на главную страницу игры.">
<meta name="theme-color" content="#0f2132">
<link rel="icon" href="/hwmicon.ico" type="image/x-icon">
<link rel="shortcut icon" href="/hwmicon.ico" type="image/x-icon">
<style>
  :root {
    --bg-deep: #08131d;
    --bg-mid: #12273a;
    --panel: rgba(8, 19, 31, 0.82);
    --panel-border: rgba(255, 255, 255, 0.12);
    --text-main: #f6efe4;
    --text-soft: #bfd0e0;
    --gold: #efb65c;
    --sky: #8fd6ff;
    --shadow: 0 30px 80px rgba(0, 0, 0, 0.36);
    --button-shadow: 0 16px 30px rgba(239, 182, 92, 0.24);
  }

  * {
    box-sizing: border-box;
  }

  html,
  body {
    margin: 0;
    min-height: 100%;
  }

  body {
    min-height: 100vh;
    min-height: 100svh;
    font-family: "Trebuchet MS", "Segoe UI", Tahoma, sans-serif;
    color: var(--text-main);
    background:
      radial-gradient(circle at top left, rgba(143, 214, 255, 0.16), transparent 30%),
      radial-gradient(circle at bottom right, rgba(239, 182, 92, 0.22), transparent 34%),
      linear-gradient(160deg, var(--bg-deep) 0%, var(--bg-mid) 100%);
    overflow-x: hidden;
  }

  body::before,
  body::after {
    content: "";
    position: fixed;
    border-radius: 50%;
    pointer-events: none;
    opacity: 0.45;
    filter: blur(16px);
  }

  body::before {
    width: 320px;
    height: 320px;
    top: -110px;
    right: -60px;
    background: radial-gradient(circle, rgba(143, 214, 255, 0.26), transparent 70%);
  }

  body::after {
    width: 380px;
    height: 380px;
    left: -100px;
    bottom: -150px;
    background: radial-gradient(circle, rgba(239, 182, 92, 0.2), transparent 72%);
  }

  .error-shell {
    width: min(1120px, 100%);
    min-height: 100vh;
    min-height: 100svh;
    margin: 0 auto;
    padding: 28px;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .error-card {
    position: relative;
    width: 100%;
    display: grid;
    grid-template-columns: minmax(0, 1.15fr) minmax(320px, 0.85fr);
    gap: 26px;
    padding: 34px;
    border-radius: 30px;
    border: 1px solid var(--panel-border);
    background: var(--panel);
    box-shadow: var(--shadow);
    backdrop-filter: blur(16px);
    overflow: hidden;
  }

  .error-card::before {
    content: "";
    position: absolute;
    inset: 1px;
    border-radius: 29px;
    background:
      linear-gradient(135deg, rgba(255, 255, 255, 0.05), transparent 34%),
      linear-gradient(315deg, rgba(143, 214, 255, 0.05), transparent 48%);
    pointer-events: none;
  }

  .error-content,
  .error-visual {
    position: relative;
    z-index: 1;
  }

  .brand-link {
    display: inline-flex;
    align-items: center;
    text-decoration: none;
  }

  .brand-logo {
    display: block;
    width: clamp(180px, 26vw, 300px);
    height: auto;
    filter: drop-shadow(0 14px 28px rgba(0, 0, 0, 0.22));
  }

  .eyebrow {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    margin-top: 26px;
    padding: 10px 16px;
    border-radius: 999px;
    border: 1px solid rgba(255, 255, 255, 0.08);
    background: rgba(255, 255, 255, 0.05);
    color: var(--text-soft);
    font-size: 13px;
    font-weight: bold;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .eyebrow::before {
    content: "";
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--gold);
    box-shadow: 0 0 0 0 rgba(239, 182, 92, 0.42);
    animation: pulse 1.8s ease-out infinite;
  }

  .error-title {
    margin: 20px 0 12px;
    max-width: 11ch;
    font-family: Georgia, "Times New Roman", serif;
    font-size: clamp(42px, 7vw, 78px);
    line-height: 0.95;
    letter-spacing: -0.04em;
  }

  .error-text {
    margin: 0;
    max-width: 36rem;
    color: var(--text-soft);
    font-size: 20px;
    line-height: 1.6;
  }

  .error-actions {
    display: flex;
    align-items: center;
    gap: 14px;
    margin-top: 32px;
    flex-wrap: wrap;
  }

  .home-button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 56px;
    padding: 0 24px;
    border-radius: 18px;
    background: linear-gradient(135deg, #ffd08a 0%, #efb65c 100%);
    color: #1c2530;
    text-decoration: none;
    font-size: 17px;
    font-weight: bold;
    box-shadow: var(--button-shadow);
    transition: transform 0.2s ease, box-shadow 0.2s ease, filter 0.2s ease;
  }

  .home-button:hover {
    transform: translateY(-2px);
    box-shadow: 0 18px 34px rgba(239, 182, 92, 0.3);
    filter: saturate(1.05);
  }

  .home-button:active {
    transform: translateY(0);
  }

  .path-note {
    color: rgba(191, 208, 224, 0.72);
    font-size: 14px;
    letter-spacing: 0.04em;
  }

  .error-visual {
    min-height: 100%;
    display: flex;
    align-items: stretch;
  }

  .visual-panel {
    position: relative;
    width: 100%;
    min-height: 430px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 26px;
    border: 1px solid rgba(255, 255, 255, 0.08);
    background:
      radial-gradient(circle at 30% 24%, rgba(255, 255, 255, 0.08), transparent 32%),
      linear-gradient(180deg, rgba(255, 255, 255, 0.04), rgba(8, 19, 31, 0.12));
    overflow: hidden;
  }

  .visual-panel::before {
    content: "";
    position: absolute;
    inset: 18px;
    border-radius: 20px;
    border: 1px solid rgba(255, 255, 255, 0.07);
  }

  .visual-grid {
    position: absolute;
    inset: 0;
    background-image:
      linear-gradient(rgba(255, 255, 255, 0.04) 1px, transparent 1px),
      linear-gradient(90deg, rgba(255, 255, 255, 0.04) 1px, transparent 1px);
    background-size: 34px 34px;
    mask-image: linear-gradient(to bottom, rgba(0, 0, 0, 0.7), transparent 92%);
    opacity: 0.35;
  }

  .visual-orb,
  .visual-orb-two {
    position: absolute;
    border-radius: 50%;
    filter: blur(2px);
  }

  .visual-orb {
    width: 230px;
    height: 230px;
    background: radial-gradient(circle, rgba(239, 182, 92, 0.2), transparent 70%);
    top: 16%;
    right: 10%;
    animation: drift 8s ease-in-out infinite;
  }

  .visual-orb-two {
    width: 180px;
    height: 180px;
    background: radial-gradient(circle, rgba(143, 214, 255, 0.18), transparent 70%);
    bottom: 10%;
    left: 10%;
    animation: drift 10s ease-in-out infinite reverse;
  }

  .visual-inner {
    position: relative;
    z-index: 1;
    width: min(100%, 390px);
    padding: 28px;
    text-align: center;
  }

  .visual-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 92px;
    min-height: 38px;
    padding: 0 14px;
    border-radius: 999px;
    border: 1px solid rgba(255, 255, 255, 0.08);
    background: rgba(255, 255, 255, 0.06);
    color: var(--text-soft);
    font-size: 12px;
    font-weight: bold;
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }

  .visual-code {
    margin: 18px 0 10px;
    font-family: Georgia, "Times New Roman", serif;
    font-size: clamp(112px, 18vw, 180px);
    line-height: 0.9;
    letter-spacing: -0.08em;
    color: var(--text-main);
    text-shadow: 0 24px 50px rgba(0, 0, 0, 0.3);
  }

  .visual-caption {
    margin: 0;
    color: var(--text-soft);
    font-size: 17px;
    line-height: 1.5;
  }

  @keyframes pulse {
    0% {
      box-shadow: 0 0 0 0 rgba(239, 182, 92, 0.42);
    }
    70% {
      box-shadow: 0 0 0 14px rgba(239, 182, 92, 0);
    }
    100% {
      box-shadow: 0 0 0 0 rgba(239, 182, 92, 0);
    }
  }

  @keyframes drift {
    0%, 100% {
      transform: translate3d(0, 0, 0) scale(1);
    }
    50% {
      transform: translate3d(0, -10px, 0) scale(1.05);
    }
  }

  @media (max-width: 920px) {
    .error-shell {
      padding: 18px;
    }

    .error-card {
      grid-template-columns: 1fr;
      gap: 18px;
      padding: 24px;
    }

    .error-content {
      text-align: center;
    }

    .brand-link,
    .error-actions {
      justify-content: center;
    }

    .error-title,
    .error-text {
      max-width: none;
    }

    .visual-panel {
      min-height: 290px;
    }
  }

  @media (max-width: 540px) {
    .error-shell {
      padding: 12px;
    }

    .error-card {
      padding: 18px;
      border-radius: 22px;
    }

    .error-card::before {
      border-radius: 21px;
    }

    .brand-logo {
      width: min(72vw, 230px);
    }

    .eyebrow {
      margin-top: 18px;
      padding: 8px 12px;
      font-size: 11px;
    }

    .error-title {
      margin-top: 14px;
      font-size: clamp(34px, 12vw, 54px);
    }

    .error-text {
      font-size: 16px;
      line-height: 1.5;
    }

    .error-actions {
      margin-top: 24px;
    }

    .home-button {
      width: 100%;
      min-height: 52px;
      font-size: 16px;
    }

    .path-note {
      width: 100%;
      text-align: center;
      font-size: 13px;
    }

    .visual-panel {
      min-height: 240px;
      border-radius: 20px;
    }

    .visual-panel::before {
      inset: 12px;
      border-radius: 14px;
    }

    .visual-inner {
      padding: 18px;
    }

    .visual-code {
      font-size: clamp(92px, 27vw, 130px);
    }

    .visual-caption {
      font-size: 15px;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .eyebrow::before,
    .visual-orb,
    .visual-orb-two,
    .home-button {
      animation: none;
      transition: none;
    }
  }
</style>
</head>
<body>
<div class="error-shell">
  <main class="error-card">
    <section class="error-content">
      <a class="brand-link" id="brandLink" href="/home.php" title="">
        <img class="brand-logo" id="brandLogo" src="/i/logos/game_logo0.png" alt="">
      </a>

      <div class="eyebrow" id="eyebrow">Ошибка 404</div>
      <h1 class="error-title" id="title">Страница потерялась</h1>
      <p class="error-text" id="description">Похоже, ссылка устарела или адрес был введён с ошибкой. Вернитесь на главную страницу игры и продолжайте приключение.</p>

      <div class="error-actions">
        <a class="home-button" id="homeButton" href="/home.php">Вернуться в игру</a>
      </div>
    </section>

    <aside class="error-visual" aria-hidden="true">
      <div class="visual-panel">
        <div class="visual-grid"></div>
        <div class="visual-orb"></div>
        <div class="visual-orb-two"></div>
        <div class="visual-inner">
          <div class="visual-badge" id="visualBadge">404</div>
          <div class="visual-code">404</div>
          <p class="visual-caption" id="visualCaption">Страница не найдена</p>
        </div>
      </div>
    </aside>
  </main>
</div>

<script>
  (function () {
    var homeHref = '/home.php';
    var i18n = {
      ru: {
        htmlLang: 'ru',
        pageTitle: 'Страница не найдена. Ошибка 404',
        descriptionMeta: 'Страница не найдена. Вернитесь на главную страницу игры.',
        brandTitle: 'Герои Войны и Денег',
        brandAlt: 'Герои Войны и Денег',
        logo: '/i/logos/game_logo0.png',
        eyebrow: 'Ошибка 404',
        title: 'Страница потерялась',
        description: 'Похоже, ссылка устарела или адрес был введён с ошибкой. Вернитесь на главную страницу игры и продолжайте приключение.',
        button: 'Вернуться в игру',
        visualCaption: 'Страница не найдена'
      },
      en: {
        htmlLang: 'en',
        pageTitle: 'Page not found. Error 404',
        descriptionMeta: 'Page not found. Return to the main game page.',
        brandTitle: 'LordsWM',
        brandAlt: 'LordsWM',
        logo: '/i/logos/game_logo1.png',
        eyebrow: 'Error 404',
        title: 'This page has gone missing',
        description: 'The link may be outdated or the address may contain a typo. Head back to the main game page and continue your adventure.',
        button: 'Back to the game',
        visualCaption: 'Page not found'
      }
    };

    function detectLang() {
      var host = (window.location.hostname || '').toLowerCase();
      if (host === 'www.lordswm.com') return 'en';
      return 'ru';
    }

    function setText(id, value) {
      var node = document.getElementById(id);
      if (node) node.textContent = value;
    }

    var lang = detectLang();
    var copy = i18n[lang] || i18n.ru;
    var descriptionNode = document.querySelector('meta[name="description"]');
    var brandLink = document.getElementById('brandLink');
    var brandLogo = document.getElementById('brandLogo');
    var homeButton = document.getElementById('homeButton');

    document.documentElement.lang = copy.htmlLang;
    document.title = copy.pageTitle;
    if (descriptionNode) descriptionNode.setAttribute('content', copy.descriptionMeta);

    if (brandLink) {
      brandLink.setAttribute('href', homeHref);
      brandLink.setAttribute('title', copy.brandTitle);
    }

    if (brandLogo) {
      brandLogo.setAttribute('src', copy.logo);
      brandLogo.setAttribute('alt', copy.brandAlt);
    }

    if (homeButton) {
      homeButton.setAttribute('href', homeHref);
      homeButton.textContent = copy.button;
    }

    setText('eyebrow', copy.eyebrow);
    setText('title', copy.title);
    setText('description', copy.description);
    setText('visualCaption', copy.visualCaption);
  })();
</script>
</body>
</html>
