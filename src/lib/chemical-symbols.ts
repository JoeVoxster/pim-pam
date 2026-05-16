export type ChemicalSymbolType = "ghs" | "adr";

export type ChemicalSymbolDefinition = {
  code: string;
  type: ChemicalSymbolType;
  label_de: string;
  label_en: string;
  src: string;
};

export const GHS_SYMBOLS: Record<string, ChemicalSymbolDefinition> = {
  GHS05: {
    code: "GHS05",
    type: "ghs",
    label_de: "Ätzend",
    label_en: "Corrosive",
    src: "/chem/ghs/GHS05.svg",
  },
  GHS09: {
    code: "GHS09",
    type: "ghs",
    label_de: "Umweltgefährlich",
    label_en: "Hazardous to the aquatic environment",
    src: "/chem/ghs/GHS09.svg",
  },
};

export const ADR_SYMBOLS: Record<string, ChemicalSymbolDefinition> = {
  ADR_8: {
    code: "ADR_8",
    type: "adr",
    label_de: "Ätzende Stoffe",
    label_en: "Corrosive substances",
    src: "/chem/adr/ADR_8.svg",
  },
  ADR_pollution: {
    code: "ADR_pollution",
    type: "adr",
    label_de: "Umweltgefährdend",
    label_en: "Environmentally hazardous",
    src: "/chem/adr/ADR_pollution.svg",
  },
};

export const CHEMICAL_SYMBOLS: Record<string, ChemicalSymbolDefinition> = {
  ...GHS_SYMBOLS,
  ...ADR_SYMBOLS,
};

export function getChemicalSymbolPath(code: string): string | undefined {
  return CHEMICAL_SYMBOLS[code]?.src;
}
