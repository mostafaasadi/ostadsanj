const ASPECT_LABELS = {
  "teaching_quality": "کیفیت تدریس", "knowledge": "دانش و تسلط", "grading": "نمره‌دهی",
  "fairness": "عدالت و انصاف", "responsiveness": "پاسخگویی", "respect": "اخلاق و احترام",
  "materials": "جزوه و منابع", "workload": "حجم تکالیف", "attendance": "حضور و غیاب",
  "exam_conduct": "برگزاری امتحان", "flexibility": "انعطاف‌پذیری", "discrimination": "تبعیض",
  "teaching": "تدریس", "communication": "ارتباط", "ethics": "اخلاق", "other": "سایر"
};

function aspectLabel(key) { return ASPECT_LABELS[key] || key; }

function scoreColor(score) {
  if (score === null || score === undefined) return "#94a3b8";
  if (score >= 60) return "#059669";
  if (score >= 30) return "#65a30d";
  if (score >= 0) return "#d97706";
  if (score >= -30) return "#ea580c";
  return "#dc2626";
}

function scoreLabel(score) {
  if (score === null || score === undefined) return "بدون داده";
  if (score >= 60) return "عالی";
  if (score >= 30) return "خوب";
  if (score >= 0) return "متوسط";
  if (score >= -30) return "ضعیف";
  return "خیلی ضعیف";
}

function faDate(dateString) {
  if (!dateString) return "-";
  const date = new Date(dateString + "T00:00:00");
  if (isNaN(date)) return dateString;
  return date.toLocaleDateString("fa-IR", { year: "numeric", month: "long", day: "numeric" });
}

function sentimentFa(sentiment) {
  return { positive: "مثبت", negative: "منفی", neutral: "خنثی", mixed: "مختلط" }[sentiment] || sentiment || "-";
}

const FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹";

function normalizeDigits(value) {
  return String(value)
    .replace(/[۰-۹]/g, digit => String(FA_DIGITS.indexOf(digit)))
    .replace(/[٠-٩]/g, digit => String("٠١٢٣٤٥٦٧٨٩".indexOf(digit)))
    .replace(/٫/g, ".");
}

function toFaDigits(value) {
  return String(value)
    .replace(/\d/g, digit => FA_DIGITS[+digit])
    .replace(/\./g, "٫");
}

function numIso(value) {
  return `<span class="ltr-num">${value}</span>`;
}

function faNum(value, options) {
  options = options || {};
  if (value === null || value === undefined || value === "" || isNaN(value)) return "-";
  const number = Number(value);
  const sign = number > 0 ? (options.signed ? "+" : "") : (number < 0 ? "-" : "");
  const absolute = Math.abs(number);
  const text = options.decimals !== undefined
    ? absolute.toFixed(options.decimals)
    : String(Math.round(absolute * 100) / 100);
  return numIso(sign + toFaDigits(text));
}

function faPct(value, options) {
  options = options || {};
  if (value === null || value === undefined || value === "" || isNaN(value)) return "-";
  const number = Math.round(Number(value) * 10) / 10;
  const sign = number > 0 ? (options.signed ? "+" : "") : (number < 0 ? "-" : "");
  return numIso(sign + toFaDigits(Math.abs(number)) + "٪");
}

function faText(value) {
  if (!value) return "";
  return normalizeDigits(value).replace(/([+-]?\d+(?:\.\d+)?)/g, match => {
    const sign = (match[0] === "+" || match[0] === "-") ? match[0] : "";
    const number = sign ? match.slice(1) : match;
    return numIso(sign + toFaDigits(number));
  });
}