import { describe, expect, it } from "vitest";
import { referenceQualificationState, type ReferenceQualification } from "./printLayout";

const ready: ReferenceQualification = {
  status: "CANDIDATE_READY", qualificationPolicy: "paged-candidate-readiness-v1",
  qualified: false, qualificationIssued: false, reviewRequired: true,
  geometryQualified: true, codes: [], productionBlockers: [], unresolvedBlockers: [],
  issuedAt: 100, expiresAt: 200,
};
const immutableReady: ReferenceQualification = {
  ...ready,
  qualificationPolicy: "paged-candidate-readiness-v2",
  validUntilChange: true,
  expiresAt: null,
};

describe("candidate qualification display", () => {
  it("accepts review-only page bindings without pretending geometry was qualified", () => {
    const reviewed: ReferenceQualification = { ...immutableReady,
      qualificationPolicy: "paged-candidate-readiness-v3",
      geometryQualified: false, pageBindingsVerified: true };
    expect(referenceQualificationState(reviewed, 120, 10_000)).toBe("CANDIDATE_READY");
    expect(referenceQualificationState({ ...reviewed, pageBindingsVerified: false }, 120, 10_000)).toBe("UNVERIFIED");
    expect(referenceQualificationState({ ...reviewed, qualified: true }, 120, 10_000)).toBe("UNVERIFIED");
  });
  it("distinguishes candidate readiness from formal PASS", () => {
    expect(referenceQualificationState(ready, undefined, 150)).toBe("CANDIDATE_READY");
    expect(referenceQualificationState(ready, undefined, 200)).toBe("EXPIRED");
  });
  it("does not let an expired formal fallback expire change-bound candidate evidence", () => {
    expect(referenceQualificationState(immutableReady, 120, 10_000)).toBe("CANDIDATE_READY");
  });
  it.each<Partial<ReferenceQualification>>([
    { validUntilChange: false }, { validUntilChange: undefined }, { expiresAt: 200 },
  ])("rejects an incomplete immutable candidate lifetime: %j", (change) => {
    expect(referenceQualificationState({ ...immutableReady, ...change }, 120, 150))
      .toBe("UNVERIFIED");
  });
  it.each<Partial<ReferenceQualification>>([
    { qualificationPolicy: "unknown" }, { qualified: true }, { qualificationIssued: true },
    { reviewRequired: false }, { geometryQualified: false }, { codes: ["CHANGED"] },
    { unresolvedBlockers: ["CHANGED"] }, { productionBlockers: ["CHANGED"] },
    { issuedAt: 151 }, { expiresAt: undefined }, { codes: undefined },
  ])("does not accept incomplete or contradictory candidate evidence: %j", (change) => {
    expect(referenceQualificationState({ ...ready, ...change }, undefined, 150)).toBe("UNVERIFIED");
  });
});
