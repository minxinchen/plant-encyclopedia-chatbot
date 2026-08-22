/**
 * Köhler public beta web app server.
 * author: Codex (GPT-5)
 * date: 2026-08-22
 */

const APP_VERSION = '2026.08.22-beta.5';
const MAX_QUESTION_LENGTH = 500;
const WINDOW_SECONDS = 600;
const REQUESTS_PER_WINDOW = 10;
const AUTH_WINDOW_SECONDS = 600;
const AUTH_ATTEMPTS_PER_WINDOW = 5;
const AUTH_SESSION_SECONDS = 21600;

function doGet() {
  return HtmlService.createTemplateFromFile('Index')
    .evaluate()
    .setTitle('Köhler 植物圖鑑聊天 beta')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

function getPublicConfig() {
  return {
    version: APP_VERSION,
    languages: ['zh-TW', 'en'],
    imageReasoning: false,
    generationProvider: 'local-qwen',
    sourcePolicy: 'Köhler book facts only; Taiwan public names are naming metadata.',
  };
}

function authenticate(password, clientToken) {
  const client = String(clientToken || 'anonymous');
  if (!consumeAuthAttempt_(client)) {
    return {ok: false, status: 'rate_limited'};
  }
  const expected = PropertiesService.getScriptProperties().getProperty('APP_PASSWORD_SHA256');
  if (!expected || sha256_(String(password || '')) !== expected) {
    return {ok: false, status: 'invalid_password'};
  }
  const token = Utilities.getUuid() + Utilities.getUuid();
  CacheService.getScriptCache().put('auth:' + sha256_(token), '1', AUTH_SESSION_SECONDS);
  return {ok: true, status: 'authenticated', token: token, expiresIn: AUTH_SESSION_SECONDS};
}

function askBook(question, language, clientToken, authToken) {
  const normalized = String(question || '').trim();
  const locale = language === 'en' ? 'en' : 'zh-TW';
  if (!isAuthorized_(authToken)) {
    return response_('authentication_required', locale === 'en'
      ? 'Enter the site password before asking a question.'
      : '請先輸入網站密碼。', [], locale);
  }
  if (!normalized || normalized.length > MAX_QUESTION_LENGTH) {
    return response_('invalid_request', locale === 'en'
      ? 'Enter a question between 1 and 500 characters.'
      : '請輸入 1 到 500 字的問題。', [], locale);
  }
  const refusal = policyRefusal_(normalized, locale);
  if (refusal) return refusal;
  if (!consumeQuota_(String(clientToken || 'anonymous'))) {
    return response_('rate_limited', locale === 'en'
      ? 'This public beta has reached its temporary request limit. Please try again later.'
      : '此公開 beta 已達暫時流量上限，請稍後再試。', [], locale);
  }
  const properties = PropertiesService.getScriptProperties();
  const gatewayUrl = properties.getProperty('LOCAL_QWEN_GATEWAY_URL');
  const gatewayToken = properties.getProperty('LOCAL_QWEN_GATEWAY_TOKEN');
  if (!gatewayUrl || !gatewayToken) {
    return response_('service_not_configured', locale === 'en'
      ? 'The public beta backend is not configured yet.'
      : '公開 beta 後端尚未完成設定。', [], locale);
  }
  try {
    const payload = {
      question: normalized,
      language: locale,
      top_k: 8,
      include_images: false,
    };
    const apiResponse = UrlFetchApp.fetch(
      gatewayUrl.replace(/\/$/, '') + '/v1/chat',
      {
        method: 'post',
        contentType: 'application/json',
        headers: {'Authorization': 'Bearer ' + gatewayToken},
        payload: JSON.stringify(payload),
        muteHttpExceptions: true,
      }
    );
    if (apiResponse.getResponseCode() !== 200) {
      console.error('Local gateway failed: HTTP ' + apiResponse.getResponseCode());
      return localOfflineResponse_(locale);
    }
    const local = JSON.parse(apiResponse.getContentText());
    const sources = localSources_(local.evidence || []);
    const answer = renderLocalAnswer_(String(local.answer || ''), local.evidence || []);
    if (local.answer_status === 'answerable_from_book' && (!answer || !citationGate_(answer))) {
      return response_('evidence_fallback', locale === 'en'
        ? 'Retrieved evidence did not pass the sentence-level page citation check, so no answer is shown.'
        : '檢索內容未通過逐句頁碼引用檢查，因此不顯示答案。', sources, locale);
    }
    return response_(local.answer_status || 'service_unavailable', answer, sources, locale);
  } catch (error) {
    console.error(error && error.stack ? error.stack : String(error));
    return localOfflineResponse_(locale);
  }
}

function localOfflineResponse_(locale) {
  return response_('local_model_offline', locale === 'en'
    ? 'The local Qwen model is offline. The computer and Qwen service must remain on for this beta.'
    : '本機 Qwen 目前離線；此 beta 需要電腦與 Qwen 服務保持開啟。', [], locale);
}

function localSources_(evidence) {
  return evidence.map(function(item) {
    return {
      fileName: 'kohler-volume-' + item.volume + ' PDF p.' + item.pdf_page,
      source: item.source_id || '',
      pageNumber: item.pdf_page || null,
    };
  });
}

function renderLocalAnswer_(answer, evidence) {
  return String(answer || '').replace(/\[E(\d+(?:\s*,\s*E\d+)*)\]/g, function(match, ids) {
    const markers = ids.split(/\s*,\s*E?/).map(function(value) {
      const item = evidence[Number(value) - 1];
      return item ? 'kohler-volume-' + item.volume + ' PDF p.' + item.pdf_page : '';
    }).filter(Boolean);
    return markers.length ? '[' + markers.join('; ') + ']' : match;
  });
}

function buildPrompt_(question, locale) {
  const language = locale === 'en' ? 'English' : 'Traditional Chinese used in Taiwan';
  const hints = queryHints_(question);
  return `You are a closed-corpus chat interface for Köhler's four-volume botanical encyclopedia.
Answer in ${language}. Use ONLY text retrieved from the File Search store. External plant-name
metadata may name a plant but may never supply uses, effects, distribution, safety, or morphology.
Prefer Taiwan public display names. If a record says non_taiwan_traditional_fallback, label it
explicitly; if unresolved, use the scientific name only. Never use Simplified Chinese unless it
appears inside a verbatim historical quote.
Copy every Latin or German drug, preparation, and substance name exactly as retrieved. Never add
a Chinese or English gloss in parentheses unless that exact pairing appears in the retrieved
metadata. Do not translate names such as Sennesblätter or Syrupus Rhamni catharticae yourself.

The source book is primarily German. Use the supplied historical German/Latin retrieval hints when
present, and search more than once when needed. For broad questions asking which plants or
preparations relate to a subject, inspect candidates independently. List a record only if its
Köhler original text directly states the requested relationship. A nearby drug/preparation list
alone is insufficient. Do not infer from retrieval rank or general knowledge.

Return no introduction and no headings. Return at most six one-line bullets. Each bullet must use
this exact shape: - scientific name, optionally followed by the Chinese display name only when the
retrieved metadata explicitly supplies it: a concise faithful statement [kohler-volume-N PDF p.N]
If Chinese display-name metadata is unresolved, do not invent or translate a Chinese name. Every
bullet MUST end with the exact page marker already present in the retrieved file. If evidence is
insufficient, output exactly INSUFFICIENT_BOOK_EVIDENCE. Medical-topic questions are allowed,
including questions phrased around the user's symptoms, but every answer must remain a faithful
historical Köhler summary. Do not add modern diagnosis, dosage, efficacy, or safety claims. Finish
with only: ${locale === 'en'
    ? 'This is a historical Köhler summary, not modern medical advice.'
    : '僅為 Köhler 歷史文獻摘要，不構成醫療建議。'}

Retrieval hints: ${hints || 'Translate the user concept into historical German and Latin search terms.'}
Question: ${question}`;
}

function queryHints_(question) {
  const folded = String(question || '').toLowerCase();
  const hints = [];
  const groups = [
    [['便秘', 'constipation'], 'Verstopfung, Obstipation, Hartleibigkeit, Leibesverstopfung, abführend, Abführmittel'],
    [['腹瀉', '腹泻', 'diarrhea'], 'Durchfall, Diarrhoe, Diarrhöe, Ruhr'],
    [['發燒', '发烧', '發熱', 'fever'], 'Fieber, Intermittens, Antipyreticum, Febrifugum'],
    [['咳嗽', 'cough'], 'Husten, Expectorans, auswurfbefördernd'],
    [['疼痛', 'pain'], 'Schmerz, Schmerzen, schmerzstillend'],
  ];
  groups.forEach(function(group) {
    if (group[0].some(function(term) { return folded.indexOf(term) !== -1; })) hints.push(group[1]);
  });
  return hints.join('; ');
}

function extractInteraction_(payload) {
  const texts = [];
  const sources = [];
  (payload.steps || []).forEach(function(step) {
    if (step.type !== 'model_output') return;
    (step.content || []).forEach(function(block) {
      if (block.type !== 'text') return;
      if (block.text) texts.push(block.text);
      (block.annotations || []).forEach(function(annotation) {
        if (annotation.type !== 'file_citation') return;
        sources.push({
          fileName: annotation.file_name || '',
          source: annotation.source || '',
          pageNumber: annotation.page_number || null,
        });
      });
    });
  });
  const unique = {};
  sources.forEach(function(item) {
    unique[item.fileName + '|' + item.source + '|' + item.pageNumber] = item;
  });
  return {text: texts.join('\n').trim(), sources: Object.keys(unique).map(function(key) {
    return unique[key];
  })};
}

function citationGate_(text) {
  if (text === 'INSUFFICIENT_BOOK_EVIDENCE') return false;
  const nonFactual = [
    '以下只列出原文直接支持問題關係的條目',
    '以下是歷史文獻記載',
    '以上是歷史文獻記載',
    '不構成醫療建議',
    '不是現代醫療',
    'not modern medical advice',
    'historical literature',
  ];
  const lines = text.split(/\n+/).map(function(line) { return line.trim(); }).filter(Boolean);
  let factual = 0;
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (nonFactual.some(function(value) { return line.toLowerCase().indexOf(value.toLowerCase()) !== -1; })) continue;
    if (/[:：]$/.test(line)) continue;
    factual += 1;
    if (!/\[kohler-volume-[1-4] PDF p\.\d+(?:; kohler-volume-[1-4] PDF p\.\d+)*\][。.!?]?$/i.test(line)) return false;
  }
  return factual > 0;
}

function policyRefusal_(question, locale) {
  const folded = question.toLowerCase();
  const outside = ['星座', '天秤座', 'zodiac', 'horoscope', 'libra'];
  const drugs = ['阿斯匹靈', '阿司匹林', 'aspirin', 'metformin', '二甲雙胍', '普拿疼', 'ibuprofen'];
  if (outside.some(function(value) { return folded.indexOf(value) !== -1; })) {
    return response_('refused_outside_book_scope', locale === 'en'
      ? 'This relationship is outside the book-verifiable scope.'
      : '這種關聯不屬於本書可查證的植物內容。', [], locale);
  }
  if (drugs.some(function(value) { return folded.indexOf(value) !== -1; })) {
    return response_('refused_non_kohler_drug', locale === 'en'
      ? 'This modern drug is outside the Köhler plant-entry scope.'
      : '這是本書植物條目以外的現代藥物。', [], locale);
  }
  return null;
}

function consumeQuota_(token) {
  const digest = Utilities.base64EncodeWebSafe(
    Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, token)
  ).slice(0, 24);
  const key = 'rate:' + digest;
  const lock = LockService.getScriptLock();
  lock.waitLock(3000);
  try {
    const cache = CacheService.getScriptCache();
    const current = Number(cache.get(key) || 0);
    if (current >= REQUESTS_PER_WINDOW) return false;
    cache.put(key, String(current + 1), WINDOW_SECONDS);
    return true;
  } finally {
    lock.releaseLock();
  }
}

function consumeAuthAttempt_(clientToken) {
  const key = 'auth-attempt:' + sha256_(clientToken);
  const lock = LockService.getScriptLock();
  lock.waitLock(3000);
  try {
    const cache = CacheService.getScriptCache();
    const current = Number(cache.get(key) || 0);
    if (current >= AUTH_ATTEMPTS_PER_WINDOW) return false;
    cache.put(key, String(current + 1), AUTH_WINDOW_SECONDS);
    return true;
  } finally {
    lock.releaseLock();
  }
}

function isAuthorized_(token) {
  if (!token) return false;
  return CacheService.getScriptCache().get('auth:' + sha256_(String(token))) === '1';
}

function sha256_(value) {
  return Utilities.base64EncodeWebSafe(
    Utilities.computeDigest(
      Utilities.DigestAlgorithm.SHA_256,
      String(value),
      Utilities.Charset.UTF_8
    )
  );
}

function response_(status, answer, sources, locale) {
  return {
    version: APP_VERSION,
    answerStatus: status,
    answer: answer,
    sources: sources || [],
    responseLocale: locale,
  };
}
