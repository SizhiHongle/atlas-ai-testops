import type {
  AssetVersionState,
  AtomCardViewModel,
  BlueprintCardViewModel,
  FixtureCatalogDto,
  FixtureCatalogViewModel
} from "./fixture";

function versionState(
  value:
    | "DRAFT"
    | "VALIDATED"
    | "PUBLISHED"
    | "DEPRECATED"
    | null
    | undefined
): AssetVersionState {
  if (value === "PUBLISHED") return "PUBLISHED";
  if (value === "VALIDATED") return "VALIDATED";
  if (value === "DEPRECATED") return "DEPRECATED";
  if (value === "DRAFT") return "DRAFT";
  return "NO_VERSION";
}

export function mapFixtureCatalog(
  dto: FixtureCatalogDto
): FixtureCatalogViewModel {
  return {
    atoms: dto.atoms.map(
      (atom): AtomCardViewModel => ({
        id: atom.id,
        key: atom.atomKey,
        name: atom.name,
        domain: atom.businessDomain,
        description: atom.description,
        version: atom.latestVersion ?? "未创建版本",
        versionState: versionState(atom.latestVersionStatus),
        definitionStatus: atom.status,
        effect: atom.latestEffect ?? "UNSPECIFIED",
        cleanupCapable: atom.cleanupCapable,
        inputPorts: [...atom.inputPorts],
        outputPorts: [...atom.outputPorts]
      })
    ),
    blueprints: dto.blueprints.map(
      (blueprint): BlueprintCardViewModel => ({
        id: blueprint.id,
        key: blueprint.blueprintKey,
        name: blueprint.name,
        description: blueprint.description,
        version: blueprint.latestVersion ?? "未创建版本",
        versionState: versionState(blueprint.latestVersionStatus),
        definitionStatus: blueprint.status,
        nodeCount: blueprint.nodeCount,
        exportCount: blueprint.exportCount,
        planDigest: blueprint.planDigest ?? null
      })
    )
  };
}
