function generateAliases(name, clerical = false) {
  const clean = name.trim().replace(/\s+/g, " ");
  if (!clean) return [];

  const parts = clean.split(" ");
  const COMPOUND_END = ["زاده", "آقایی", "نژاد", "پناه", "دوست", "منش", "فر", "پور", "راد", "مند", "وند", "یان", "ها"];

  let family = parts[parts.length - 1];
  if (parts.length >= 3 && COMPOUND_END.includes(family)) {
    family = parts.slice(-2).join(" ");
  }

  const familyVariants = new Set([family]);
  const halfSpace = family.replace(" ", "‌");
  const noSpace = family.replace(/ /g, "");
  if (halfSpace !== family) familyVariants.add(halfSpace);
  if (noSpace !== family) familyVariants.add(noSpace);

  const aliases = new Set();
  aliases.add(clean);
  for (const fv of familyVariants) {
    aliases.add(fv);
    aliases.add("دکتر " + fv);
    aliases.add("استاد " + fv);
    if (clerical) {
      aliases.add("حجت الاسلام " + fv);
      aliases.add("حجت الاسلام و المسلمین " + fv);
      aliases.add("شیخ " + fv);
    }
  }
  aliases.add("#" + family.replace(/ /g, ""));
  aliases.add("#" + clean.replace(/ /g, "_"));

  return [...aliases];
}