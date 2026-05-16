import { ADR_SYMBOLS, CHEMICAL_SYMBOLS, GHS_SYMBOLS } from "../lib/chemical-symbols";

type ChemSafety = {
  ghs_pictograms?: string[];
  signal_word?: "none" | "warning" | "danger" | string;
  adr_pictograms?: string[];
  adr_class?: string;
  environmentally_hazardous?: boolean;
};

type ProductLike = {
  metadata?: {
    chem_safety?: ChemSafety;
  };
  chemical_safety_json?: ChemSafety;
};

type Props = {
  product: ProductLike;
  locale?: string;
};

function labelFor(code: string, locale?: string): string {
  const symbol = CHEMICAL_SYMBOLS[code];
  if (!symbol) return code;
  return String(locale || "").toLowerCase().startsWith("en") ? symbol.label_en : symbol.label_de;
}

function signalWordLabel(signalWord?: string, locale?: string): string | null {
  if (!signalWord || signalWord === "none") return null;
  const english = String(locale || "").toLowerCase().startsWith("en");
  if (signalWord === "danger") return english ? "Danger" : "Gefahr";
  if (signalWord === "warning") return english ? "Warning" : "Achtung";
  return signalWord;
}

export function ProductSafetyPictograms({ product, locale }: Props) {
  const safety = product.metadata?.chem_safety || product.chemical_safety_json;
  if (!safety) return null;

  const ghsCodes = (safety.ghs_pictograms || []).filter((code) => GHS_SYMBOLS[code]);
  const adrCodes = (safety.adr_pictograms || []).filter((code) => ADR_SYMBOLS[code]);
  const signalWord = signalWordLabel(safety.signal_word, locale);

  if (!ghsCodes.length && !adrCodes.length && !signalWord) return null;

  return (
    <section aria-label="Product safety pictograms">
      {signalWord ? <strong>{signalWord}</strong> : null}
      {ghsCodes.length ? (
        <div>
          <h3>GHS</h3>
          {ghsCodes.map((code) => (
            <figure key={code}>
              <img src={CHEMICAL_SYMBOLS[code].src} alt={labelFor(code, locale)} width={64} height={64} />
              <figcaption>{labelFor(code, locale)}</figcaption>
            </figure>
          ))}
        </div>
      ) : null}
      {adrCodes.length ? (
        <div>
          <h3>ADR</h3>
          {adrCodes.map((code) => (
            <figure key={code}>
              <img src={CHEMICAL_SYMBOLS[code].src} alt={labelFor(code, locale)} width={64} height={64} />
              <figcaption>{labelFor(code, locale)}</figcaption>
            </figure>
          ))}
        </div>
      ) : null}
    </section>
  );
}
