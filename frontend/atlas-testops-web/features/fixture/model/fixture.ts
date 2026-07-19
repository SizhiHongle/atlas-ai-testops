import type { components } from "@/shared/api/schema";

export type DataAtomCatalogItemDto =
  components["schemas"]["DataAtomCatalogItem"];
export type DataBlueprintCatalogItemDto =
  components["schemas"]["DataBlueprintCatalogItem"];
export type CreateDataAtomCommand = components["schemas"]["CreateDataAtom"];
export type CreateDataBlueprintCommand =
  components["schemas"]["CreateDataBlueprint"];
export type DataAtomDefinitionDto =
  components["schemas"]["DataAtomDefinition"];
export type DataBlueprintDefinitionDto =
  components["schemas"]["DataBlueprintDefinition"];

export type FixtureCatalogDto = {
  atoms: DataAtomCatalogItemDto[];
  blueprints: DataBlueprintCatalogItemDto[];
};

export type AssetVersionState =
  | "PUBLISHED"
  | "VALIDATED"
  | "DRAFT"
  | "DEPRECATED"
  | "NO_VERSION";

export type AtomCardViewModel = {
  id: string;
  key: string;
  name: string;
  domain: string;
  description: string;
  version: string;
  versionState: AssetVersionState;
  definitionStatus: string;
  effect: string;
  cleanupCapable: boolean;
  inputPorts: string[];
  outputPorts: string[];
};

export type BlueprintCardViewModel = {
  id: string;
  key: string;
  name: string;
  description: string;
  version: string;
  versionState: AssetVersionState;
  definitionStatus: string;
  nodeCount: number;
  exportCount: number;
  planDigest: string | null;
};

export type FixtureCatalogViewModel = {
  atoms: AtomCardViewModel[];
  blueprints: BlueprintCardViewModel[];
};
