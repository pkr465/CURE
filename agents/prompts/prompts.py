"""
LLM prompt templates for C/C++ codebase analysis
"""

from typing import Dict, Any, List


class PromptTemplates:
    """
    Contains all LLM prompt templates for C/C++ codebase analysis.

    Templates are designed to provide context-aware prompts for different
    analysis stages, optimized for C/C++ specific concerns and professional
    reporting.

    NOTE:
    - Many prompts now require the model to return a final, single JSON object
      with a clearly defined schema. This is intended to be ingestible by both
      humans (as structured narrative) and coding agents (for automation).
    """

    @staticmethod
    def get_codebase_insights_prompt(file_cache: List[Dict[str, Any]]) -> str:
        """Generate prompt for overall codebase insights."""
        total_files = len(file_cache)
        languages: Dict[str, int] = {}
        total_lines = 0
        total_functions = 0

        for file_entry in file_cache:
            lang = file_entry.get("language", "unknown")
            languages[lang] = languages.get(lang, 0) + 1

            metrics = file_entry.get("metrics", {})
            total_lines += metrics.get("total_lines", 0)

            functions = file_entry.get("functions", [])
            total_functions += len(functions)

        sample_files = [f.get("file_relative_path", "") for f in file_cache[:10]]

        return f"""
You are a senior C/C++ software architect performing a high-level assessment of a codebase.

Context:
- Total files: {total_files}
- Languages: {dict(languages)}
- Total lines of code: {total_lines}
- Total functions: {total_functions}

Sample file structure:
{chr(10).join(f"- {path}" for path in sample_files)}

Objectives:
- Provide a professional, concise, and technically accurate assessment.
- Your analysis should be directly useful to both:
  - Human architects and technical leaders.
  - Automated coding agents, which will consume the structured JSON output.

Please perform the following analysis:

1) Architecture Assessment
   - Infer the overall architecture and prevalent design patterns based on the file structure and language distribution.
   - Identify any apparent layering (e.g., platform/driver, core logic, interfaces, tests) and potential violations.

2) Development Practices
   - Infer development and coding practices from the structure (testing discipline, documentation culture, modularity, reuse).
   - Highlight strengths and weaknesses relevant to professional C/C++ development.

3) Potential Concerns
   - Identify key areas of concern that warrant deeper investigation (e.g., monolithic design, tight coupling, missing tests, unsafe constructs).
   - Emphasize C/C++-specific concerns such as build complexity, ABI compatibility, memory management, and portability.

4) Recommendations
   - Provide prioritized, concrete recommendations that can inform a medium-to-long-term improvement plan.
   - Recommendations should be specific enough to be turned into work items.

5) Testing Strategy
   - Propose a high-level testing and validation strategy aligned to the inferred risks:
     - Unit tests, integration tests, system tests.
     - Fuzzing, static analysis, dynamic analysis.
   - Suggest how the organization can measure progress.

Important response format requirements:
- Provide a short, professional narrative if necessary.
- Then, at the end of the response, output a single JSON object and nothing after it.
- The JSON must strictly follow this schema:

{{
  "architecture_assessment": {{
    "summary": "string",
    "observations": ["string"]
  }},
  "issues": [
    {{
      "id": "ARCH-ISSUE-1",
      "title": "Short title",
      "description": "Detailed C/C++ specific issue description",
      "context": "Where/when this typically occurs in such codebases",
      "severity": "low|medium|high|critical",
      "impact": "Developer productivity / runtime reliability / security / build times / etc."
    }}
  ],
  "proposed_solutions": [
    {{
      "issue_id": "ARCH-ISSUE-1",
      "summary": "Short summary of the solution",
      "steps": [
        "Actionable step 1",
        "Actionable step 2"
      ],
      "implementation_hints": [
        "Concrete hints a coding agent can follow (e.g., introduce interface X, split module Y, add unit tests for module Z)"
      ]
    }}
  ],
  "risks_and_concerns": [
    {{
      "category": "architecture|performance|maintainability|testing|security|build",
      "description": "Risk description",
      "likelihood": "low|medium|high",
      "impact": "low|medium|high|critical",
      "mitigations": ["string"]
    }}
  ],
  "recommendations": [
    {{
      "id": "ARCH-REC-1",
      "title": "Short recommendation name",
      "priority": "P0|P1|P2",
      "description": "What to do and why",
      "related_issues": ["ARCH-ISSUE-1"]
    }}
  ],
  "testing_plan": {{
    "overview": "High-level description of testing strategy",
    "phases": [
      {{
        "name": "Phase name (e.g., Establish baseline unit tests)",
        "goals": ["string"],
        "test_types": ["unit", "integration", "system", "fuzzing", "static_analysis", "dynamic_analysis"],
        "actions": [
          "Actionable testing step 1",
          "Actionable testing step 2"
        ]
      }}
    ]
  }}
}}
""".strip()

    @staticmethod
    def get_dependency_analysis_prompt(dependency_graph: Dict[str, Any]) -> str:
        """Generate prompt for dependency analysis."""
        analysis = dependency_graph.get("analysis", {})

        total_nodes = analysis.get("total_nodes", 0)
        internal_nodes = analysis.get("internal_nodes", 0)
        external_nodes = analysis.get("external_nodes", 0)
        has_cycles = analysis.get("has_cycles", False)
        cycle_count = analysis.get("cycle_count", 0)
        max_fan_out = analysis.get("max_fan_out", 0)

        sample_modules: List[str] = []
        count = 0
        for module_name, module_data in dependency_graph.items():
            if module_name != "analysis" and count < 5:
                deps = len(module_data.get("dependencies", []))
                sample_modules.append(f"- {module_name}: {deps} dependencies")
                count += 1

        return f"""
You are a senior C/C++ software architect analyzing dependency relationships in a large-scale codebase.

Dependency metrics summary:
- Total modules: {total_nodes}
- Internal modules: {internal_nodes}
- External dependencies: {external_nodes}
- Circular dependencies: {"Yes" if has_cycles else "No"} ({cycle_count} cycles)
- Maximum fan-out: {max_fan_out}

Sample modules:
{chr(10).join(sample_modules)}

Objectives:
- Provide a highly professional, in-depth analysis of the dependency structure.
- Produce output suitable for:
  - Architects and technical leads, and
  - Automated coding agents that will use the structured output to drive refactorings.

Please provide analysis for:

1) Dependency Health
   - Assess overall dependency health for this C/C++ project.
   - Comment on potential “god” modules, heavily shared headers, and deep include chains.

2) Coupling Analysis
   - Interpret fan-out and fan-in to identify tight coupling and high-risk hotspots.
   - Highlight problematic coupling patterns (e.g., cross-layer dependencies, cyclic includes).

3) Circular Dependencies
   - {"Analyze the impact of circular dependencies on compilation, linking, testing, and refactoring. Propose strategies to systematically eliminate these cycles." if has_cycles else "Explain the benefits of having no circular dependencies, and provide guidance on how to preserve this property as the codebase evolves."}

4) Architecture Implications
   - Describe what the dependency structure suggests about layering, boundaries, and modular design.
   - Identify clear violations of standard architectural principles (e.g., infrastructure depending on UI).

5) Refactoring Opportunities
   - Propose specific, high-value refactoring opportunities:
     - e.g., “extract interface”, “introduce adapter layer”, “split monolithic module”, “move shared declarations into dedicated headers”.

6) Alignment with C/C++ Best Practices
   - Evaluate how the current structure aligns with C/C++ best practices such as:
     - header vs. source separation,
     - use of forward declarations,
     - pimpl idiom,
     - stable module interfaces.

7) Build System Impact
   - Explain likely implications for build times, incremental builds, and CI stability.
   - Suggest concrete build-oriented improvements (e.g., modulated CMake targets, precompiled headers, limiting widely included headers).

8) Testing Plan for Dependency Refactors
   - Outline a testing/validation approach for dependency-related refactoring:
     - compilation checks,
     - unit/integration/regression tests,
     - configuration and ABI compatibility checks.

Important response format requirements:
- You may provide a short narrative, but the primary output must be a single JSON object at the end.
- The JSON must strictly follow this schema, and nothing should follow it:

{{
  "architecture_assessment": {{
    "summary": "string",
    "observations": ["string"]
  }},
  "issues": [
    {{
      "id": "DEP-ISSUE-1",
      "title": "Short title",
      "description": "Detailed description of the dependency issue",
      "context": "Where/when this occurs (e.g., cross-layer dependency between core and UI)",
      "severity": "low|medium|high|critical",
      "impact": "build_times|maintainability|testability|runtime_risk|other"
    }}
  ],
  "proposed_solutions": [
    {{
      "issue_id": "DEP-ISSUE-1",
      "summary": "Short solution summary",
      "steps": [
        "Concrete step 1 (e.g., split header A into A_iface.h and A_impl.h)",
        "Concrete step 2"
      ],
      "implementation_hints": [
        "Specific hints for a coding agent (e.g., introduce pure virtual interfaces, move implementation-only includes to .cpp)"
      ]
    }}
  ],
  "risks_and_concerns": [
    {{
      "category": "architecture|build|regression|complexity",
      "description": "Risk description",
      "likelihood": "low|medium|high",
      "impact": "low|medium|high|critical",
      "mitigations": ["string"]
    }}
  ],
  "recommendations": [
    {{
      "id": "DEP-REC-1",
      "title": "Short recommendation title",
      "priority": "P0|P1|P2",
      "description": "What to do and why",
      "related_issues": ["DEP-ISSUE-1"]
    }}
  ],
  "testing_plan": {{
    "overview": "How to validate dependency refactors",
    "phases": [
      {{
        "name": "Phase 1 - Safe refactors & build verification",
        "goals": ["string"],
        "test_types": ["build", "unit", "integration"],
        "actions": [
          "Run full rebuild on all supported toolchains",
          "Execute unit and integration tests for impacted modules",
          "Add tests specifically around refactored interfaces"
        ]
      }}
    ]
  }}
}}
""".strip()

    @staticmethod
    def get_health_metrics_analysis_prompt(health_metrics: Dict[str, Any]) -> str:
        """Generate prompt for health metrics analysis (per-metric, structured)."""
        scores: Dict[str, Dict[str, Any]] = {}
        for metric_name, metric_data in health_metrics.items():
            if isinstance(metric_data, dict) and "score" in metric_data:
                scores[metric_name] = {
                    "score": metric_data.get("score", 0),
                    "grade": metric_data.get("grade", "F"),
                    "issues": len(metric_data.get("issues", [])),
                }

        overall_health = health_metrics.get("overall_health", {})
        overall_score = overall_health.get("score", 0)
        overall_grade = overall_health.get("grade", "F")

        scores_lines = [
            f"- {name}: {data['score']}/100 ({data['grade']}) - {data['issues']} issues"
            for name, data in scores.items()
        ]

        return f"""
You are a C/C++ code quality expert analyzing detailed health metrics for a mature codebase.

Overall health:
- Score: {overall_score}/100
- Grade: {overall_grade}

Individual metric scores:
{chr(10).join(scores_lines)}

Available metrics (if present in the input):
- dependency_score
- quality_score
- complexity_score
- maintainability_score
- documentation_score
- test_coverage_score
- security_score
- overall_health

Objectives:
- Provide a highly professional, per-metric analysis.
- For each metric present, your analysis must include:
  - Issue description.
  - Proposed solution (implementation-ready, stepwise).
  - Architectural / issue assessment.
  - Risks and concerns.
  - Recommendations (prioritized).
  - Actionable testing plan.

Guidance:
- Use C/C++-specific terminology and best practices.
- Focus on changes that realistically improve the codebase over time.
- Distinguish between quick wins and structural efforts.

Important response format requirements:
- You may include a brief narrative, but the primary output must be a single JSON object at the end.
- The JSON must follow this schema (include only metrics that exist in health_metrics):

{{
  "metrics": {{
    "<metric_name>": {{
      "issues": [
        {{
          "id": "METRIC-ISSUE-1",
          "description": "string",
          "severity": "low|medium|high|critical",
          "impact": "string"
        }}
      ],
      "proposed_solutions": [
        {{
          "issue_id": "METRIC-ISSUE-1",
          "steps": ["string"],
          "implementation_hints": ["string"]
        }}
      ],
      "architecture_assessment": {{
        "summary": "string",
        "observations": ["string"]
      }},
      "risks_and_concerns": [
        {{
          "description": "string",
          "likelihood": "low|medium|high",
          "impact": "low|medium|high|critical"
        }}
      ],
      "recommendations": [
        {{
          "id": "METRIC-REC-1",
          "priority": "P0|P1|P2",
          "description": "string",
          "related_issues": ["METRIC-ISSUE-1"]
        }}
      ],
      "testing_plan": {{
        "overview": "string",
        "actions": ["string"]
      }}
    }}
  }},
  "overall": {{
    "priority_roadmap": [
      {{
        "step": 1,
        "focus_area": "security|complexity|maintainability|dependencies|tests|documentation",
        "description": "string"
      }}
    ]
  }}
}}
Notes:
- Replace <metric_name> with actual keys (e.g., "dependency_score", "security_score") that are present in health_metrics.
- Do not include metrics that are not present in the input.
""".strip()

    @staticmethod
    def get_documentation_recommendations_prompt(
        documentation_analysis: Dict[str, Any]
    ) -> str:
        """Generate prompt for documentation recommendations."""
        base_docs = documentation_analysis.get("base_documentation", {})
        overview = base_docs.get("overview", {})

        total_modules = overview.get("total_modules", 0)
        internal_modules = overview.get("internal_modules", 0)
        external_deps = overview.get("external_dependencies", 0)

        return f"""
You are a technical documentation specialist for professional C/C++ projects.

Project overview:
- Total modules: {total_modules}
- Internal modules: {internal_modules}
- External dependencies: {external_deps}

Objectives:
- Provide a professional, comprehensive assessment of documentation gaps and opportunities.
- Produce recommendations that can be executed by both humans and coding agents.
- Include issue descriptions, proposals, risks, and a documentation-focused testing/validation plan.

Please cover:

1) API Documentation
   - Standards (e.g., Doxygen) and conventions suitable for this codebase.
   - Minimum expectations for public and internal APIs.

2) Header Documentation
   - Documentation of ownership, lifetime, threading model, error handling, and invariants.
   - Guidance for documenting macros, inline functions, and templates.

3) Build & Tooling Documentation
   - Documentation of build systems, toolchains, configuration, and platform variations.

4) Architecture Documentation
   - Recommended architectural views (module diagrams, data flow, concurrency model).
   - How to keep such documentation aligned with the code.

5) Developer Onboarding
   - Documentation that accelerates onboarding for new C/C++ developers.

6) Maintenance & Operational Documentation
   - Documentation that supports long-term maintenance, debugging, incident response, and releases.

7) Integration & External Dependencies
   - Documentation of third-party libraries, ABI considerations, and compatibility expectations.

8) Examples & Tutorials
   - Types of code samples and usage patterns that should be provided and kept verified.

Important response format requirements:
- Optionally provide a brief narrative.
- Then output a single JSON object and nothing after it, matching this schema:

{{
  "issues": [
    {{
      "id": "DOC-ISSUE-1",
      "area": "api|headers|build|architecture|onboarding|maintenance|integration|examples",
      "description": "Documentation gap or weakness",
      "severity": "low|medium|high|critical",
      "impact": "onboarding|maintainability|operability|risk"
    }}
  ],
  "proposed_solutions": [
    {{
      "issue_id": "DOC-ISSUE-1",
      "summary": "Short solution summary",
      "steps": [
        "Concrete step 1 (e.g., add Doxygen comments for all public functions in module X)",
        "Concrete step 2"
      ],
      "implementation_hints": [
        "Hints for a coding agent (e.g., for each header in include/, ensure @brief/@param/@return are present)"
      ]
    }}
  ],
  "architecture_assessment": {{
    "summary": "How current documentation reflects or obscures the architecture",
    "observations": ["string"]
  }},
  "risks_and_concerns": [
    {{
      "description": "Risk from poor or missing documentation",
      "likelihood": "low|medium|high",
      "impact": "low|medium|high|critical",
      "mitigations": ["string"]
    }}
  ],
  "recommendations": [
    {{
      "id": "DOC-REC-1",
      "priority": "P0|P1|P2",
      "description": "Specific documentation initiative",
      "related_issues": ["DOC-ISSUE-1"]
    }}
  ],
  "testing_plan": {{
    "overview": "How to validate documentation improvements",
    "actions": [
      "Introduce documentation coverage checks in CI (e.g., percentage of public APIs documented)",
      "Regularly verify that code examples compile and run as part of tests",
      "Perform periodic focused doc reviews for top-risk modules"
    ]
  }}
}}
""".strip()

    @staticmethod
    def get_modularization_plan_prompt(
        dependency_graph: Dict[str, Any],
        base_plan: Dict[str, Any],
    ) -> str:
        """Generate prompt for modularization planning."""
        analysis = dependency_graph.get("analysis", {})

        action_counts: Dict[str, int] = {}
        for module_name, plan_data in base_plan.items():
            action = plan_data.get("action", "unknown")
            action_counts[action] = action_counts.get(action, 0) + 1

        actions_lines = [
            f"- {action}: {count} modules" for action, count in action_counts.items()
        ]

        return f"""
You are a senior C/C++ software architect refining a modularization plan.

Current dependency metrics:
- Total modules: {analysis.get('total_nodes', 0)}
- Circular dependencies: {analysis.get('cycle_count', 0)}
- Max fan-out: {analysis.get('max_fan_out', 0)}

Summary of proposed actions:
{chr(10).join(actions_lines)}

Objectives:
- Enhance the modularization plan with precise, actionable guidance.
- Output must:
  - Reflect sound C/C++ architectural principles.
  - Be directly usable by coding agents implementing the changes.

Please provide:

1) Target Module Boundaries
   - Describe the desired module boundaries and layering (e.g., platform, core, services, API).
   - Clarify how to enforce cohesion and reduce coupling.

2) Header & Interface Strategy
   - Propose how to organize headers:
     - public vs internal headers,
     - stable APIs vs implementation details.
   - Recommend interface patterns (pimpl, abstract base classes, adapters) suitable for this codebase.

3) Compilation Unit Strategy
   - Recommend how to structure translation units (e.g., per-module .cpps, limiting mega-translation units).
   - Explain benefits in terms of compilation times and isolation.

4) Migration Plan
   - Provide a phased, low-risk migration strategy from the current structure to the desired modular architecture.
   - Each phase should have clear, implementable actions.

5) Build System Adaptation
   - Recommend changes to build configuration (CMake/Make/Bazel/etc.) to reflect new modules.
   - Discuss independent targets, libraries, and link-time considerations.

6) Testing Strategy for Modularization
   - Define how to validate modularization changes without regressions:
     - regression tests,
     - module-level tests,
     - contract/interface tests.

7) Performance and Footprint Considerations
   - Call out performance and memory footprint risks and mitigations associated with the modularization.

Important response format requirements:
- Provide a brief, professional narrative only if needed.
- Then output a single JSON object matching the schema below, with nothing after it:

{{
  "architecture_assessment": {{
    "summary": "string",
    "target_structure": ["description of layers/modules"],
    "observations": ["string"]
  }},
  "issues": [
    {{
      "id": "MOD-ISSUE-1",
      "description": "Current structural/modular problem",
      "severity": "low|medium|high|critical",
      "impact": "maintainability|build|testability|performance"
    }}
  ],
  "proposed_solutions": [
    {{
      "issue_id": "MOD-ISSUE-1",
      "summary": "Short solution summary",
      "steps": [
        "Concrete step 1",
        "Concrete step 2"
      ],
      "implementation_hints": [
        "Hints for coding agent (e.g., create new library target 'libcore_net' and move files X,Y there)"
      ]
    }}
  ],
  "migration_plan": {{
    "phases": [
      {{
        "name": "Phase 1 - Identify and isolate core modules",
        "goals": ["string"],
        "actions": ["string"]
      }}
    ]
  }},
  "risks_and_concerns": [
    {{
      "description": "Risk from modularization",
      "likelihood": "low|medium|high",
      "impact": "low|medium|high|critical",
      "mitigations": ["string"]
    }}
  ],
  "recommendations": [
    {{
      "id": "MOD-REC-1",
      "priority": "P0|P1|P2",
      "description": "Strategic modularization recommendation",
      "related_issues": ["MOD-ISSUE-1"]
    }}
  ],
  "testing_plan": {{
    "overview": "How to test modularization changes",
    "phases": [
      {{
        "name": "Phase 1 - Baseline regression",
        "goals": ["string"],
        "test_types": ["unit", "integration", "system"],
        "actions": [
          "Run full regression after each major structural change",
          "Introduce contract tests for key module interfaces"
        ]
      }}
    ]
  }}
}}
""".strip()

    @staticmethod
    def get_validation_insights_prompt(
        validation_report: Dict[str, Any],
        modularization_plan: Dict[str, Any],
    ) -> str:
        """Generate prompt for validation insights."""
        base_validation = validation_report.get("base_validation", {})
        overall_score = base_validation.get("overall_score", 0)
        issues = base_validation.get("issues", [])

        issues_lines = [f"- {issue}" for issue in issues[:5]]

        return f"""
You are a C/C++ software quality and architecture expert validating a modularization plan.

Validation results:
- Overall score: {overall_score}/100
- Issues identified: {len(issues)}

Sample key issues:
{chr(10).join(issues_lines)}

Objectives:
- Provide a professional, risk-focused validation assessment.
- Output must be clear enough for leadership and precise enough for coding agents.

Please address:

1) Risk Assessment
   - Identify the highest risks associated with the current modularization plan,
     including hidden dependencies, ABI breaks, regression risk, and performance changes.

2) Success Criteria
   - Define specific, measurable success criteria (e.g., reductions in build time, defect rate, complexity).

3) Quality Gates
   - Propose quality gates during modularization:
     - coverage thresholds,
     - allowed number of dependency cycles,
     - acceptable static analysis warning levels.

4) Rollback Strategy
   - Describe how to roll back modularization changes safely if significant issues are discovered.

5) Monitoring & Metrics
   - Identify key technical and process metrics to monitor during and after modularization.

6) Team & Process Impact
   - Highlight impacts on development workflows, code ownership, and review practices.

7) Tooling Requirements
   - Recommend tools (dependency visualizers, static analyzers, CI checks) necessary to support safe modularization.

8) Timeline Considerations
   - Provide guidance on realistic timelines and sequencing for C/C++ modularization efforts.

Important response format requirements:
- You may include a concise commentary.
- Then output a single JSON object with the following structure, and nothing after it:

{{
  "risk_assessment": [
    {{
      "id": "VAL-RISK-1",
      "description": "Risk description",
      "likelihood": "low|medium|high",
      "impact": "low|medium|high|critical",
      "mitigations": ["string"]
    }}
  ],
  "success_criteria": [
    {{
      "id": "VAL-SC-1",
      "description": "Success metric or criteria",
      "metric_type": "build_time|defects|coverage|complexity|other",
      "target_value": "string"
    }}
  ],
  "quality_gates": [
    {{
      "id": "VAL-QG-1",
      "description": "Quality gate description",
      "metric": "coverage|static_analysis_warnings|dependency_cycles|other",
      "threshold": "string"
    }}
  ],
  "rollback_strategy": {{
    "overview": "How to roll back safely",
    "steps": ["string"]
  }},
  "monitoring_plan": {{
    "metrics": ["string"],
    "reporting_frequency": "string",
    "responsible_roles": ["string"]
  }},
  "recommendations": [
    {{
      "id": "VAL-REC-1",
      "priority": "P0|P1|P2",
      "description": "Validation-related recommendation"
    }}
  ],
  "testing_plan": {{
    "overview": "Validation testing plan",
    "phases": [
      {{
        "name": "Phase 1 - Pre-modularization baseline",
        "goals": ["string"],
        "actions": ["string"]
      }},
      {{
        "name": "Phase 2 - Incremental validation of modular changes",
        "goals": ["string"],
        "actions": ["string"]
      }}
    ]
  }}
}}
""".strip()

    @staticmethod
    def get_final_report_prompt(
        summary: Dict[str, Any],
        dependency_graph: Dict[str, Any],
        documentation: Dict[str, Any],
        modularization_plan: Dict[str, Any],
        validation_report: Dict[str, Any],
        health_metrics: Dict[str, Any],
    ) -> str:
        """Generate prompt for final comprehensive report."""
        file_stats = summary.get("file_stats", {})
        total_files = file_stats.get("total_files", 0)
        languages = file_stats.get("languages", {})

        overall_health = health_metrics.get("overall_health", {})
        health_score = overall_health.get("score", 0)
        health_grade = overall_health.get("grade", "F")

        dep_analysis = dependency_graph.get("analysis", {})
        has_cycles = dep_analysis.get("has_cycles", False)

        return f"""
You are an expert C/C++ software architect preparing an executive-level final report for stakeholders.

Codebase overview:
- Total files: {total_files}
- Languages: {dict(languages)}
- Overall Health: {health_score}/100 ({health_grade})
- Circular Dependencies: {"Yes" if has_cycles else "No"}

Objectives:
- Provide a professional, concise executive summary that is technically accurate.
- Output should drive decision-making at the leadership level and be structured enough for tooling and coding agents.

Your report should cover:

1) Executive Summary
   - High-level assessment of codebase health, major strengths, and major weaknesses.

2) Critical Issues
   - 5–10 critical issues across security, architecture, reliability, complexity, test coverage, and maintainability.

3) Strategic Recommendations
   - Long-term improvement themes and strategic initiatives.

4) Implementation Roadmap
   - A phased roadmap (short-, medium-, and long-term).
   - Each phase should have clear goals and key actions.

5) Resource & Effort Estimates
   - High-level estimates of effort (small/medium/large) and skill requirements.

6) Risk Assessment
   - Key risks of not acting.
   - Key risks associated with major changes.

7) Success Metrics
   - How to measure success of the improvement program (technical and process metrics).

8) Technology Modernization Opportunities
   - Opportunities for modernization (e.g., newer C++ standards, improved tooling).

9) Team & Process Recommendations
   - Recommendations around practices, reviews, ownership, and skills.

10) Monitoring Strategy
    - How to continuously monitor health and prevent regressions.

Important response format requirements:
- You may write a concise executive narrative if desired.
- Then produce a single JSON object and nothing after it, conforming exactly to this schema:

{{
  "executive_summary": {{
    "overview": "string",
    "key_findings": ["string"]
  }},
  "critical_issues": [
    {{
      "id": "CRIT-1",
      "title": "Short issue name",
      "description": "string",
      "category": "security|architecture|quality|tests|performance|build|other",
      "severity": "low|medium|high|critical",
      "impact": "string"
    }}
  ],
  "strategic_recommendations": [
    {{
      "id": "STRAT-1",
      "title": "Recommendation name",
      "description": "string",
      "priority": "P0|P1|P2",
      "related_issues": ["CRIT-1"]
    }}
  ],
  "roadmap": [
    {{
      "phase": 1,
      "name": "Phase name",
      "time_horizon": "short|medium|long",
      "goals": ["string"],
      "key_actions": ["string"]
    }}
  ],
  "resource_requirements": [
    {{
      "id": "RES-1",
      "description": "Needed skills/resources",
      "size": "small|medium|large"
    }}
  ],
  "risks_and_concerns": [
    {{
      "description": "Risk description",
      "likelihood": "low|medium|high",
      "impact": "low|medium|high|critical",
      "mitigations": ["string"]
    }}
  ],
  "success_metrics": [
    {{
      "id": "MET-1",
      "description": "What to measure",
      "metric_type": "coverage|defects|build_time|performance|security|other",
      "target_value": "string"
    }}
  ],
  "modernization_opportunities": [
    {{
      "id": "MOD-1",
      "description": "Modernization opportunity (e.g., adopt C++20 in module X)"
    }}
  ],
  "team_and_process_recommendations": [
    {{
      "id": "TEAM-1",
      "description": "Team/process improvement"
    }}
  ],
  "monitoring_strategy": {{
    "overview": "string",
    "metrics": ["string"],
    "cadence": "string"
  }}
}}
""".strip()

    @staticmethod
    def get_security_focus_prompt(security_metrics: Dict[str, Any]) -> str:
        """Generate prompt focused on security analysis."""
        security_score = security_metrics.get("score", 0)
        security_grade = security_metrics.get("grade", "F")
        issues = security_metrics.get("issues", [])

        issues_lines = [f"- {issue}" for issue in issues[:5]]

        return f"""
You are a C/C++ application and systems security expert performing a focused security assessment.

Security assessment summary:
- Security Score: {security_score}/100 ({security_grade})
- Issues identified: {len(issues)}

Sample top security concerns:
{chr(10).join(issues_lines)}

Objectives:
- Provide a highly professional, in-depth security analysis.
- Output should support both security engineers and coding agents performing remediations.

Please address:

1) Memory Safety
   - Identify and characterize memory safety risks (dangling pointers, use-after-free, double free, invalid lifetime).

2) Buffer Management & Overflows
   - Highlight risks from buffer handling (stack/heap), unsafe APIs, and missing bounds checks.

3) Input Validation & Sanitization
   - Comment on likely input validation weaknesses and data sanitization issues.

4) Secure Coding Practices
   - Recommend relevant secure coding standards and conventions for this codebase (e.g., CERT C, MISRA, internal guidelines).

5) Static & Dynamic Analysis
   - Propose concrete static and dynamic analysis tooling and how to integrate it into CI.

6) Code Review Focus
   - Define what security-focused code reviews should explicitly look for.

7) Vulnerability Remediation Plan
   - Provide a prioritized remediation roadmap, ordered by risk and exploitability.

8) Security Testing Plan
   - Recommend fuzzing, penetration testing, negative testing, and regression testing strategies.

Important response format requirements:
- You may include a brief professional commentary.
- Then output a single JSON object and nothing after it, respecting this schema:

{{
  "issues": [
    {{
      "id": "SEC-ISSUE-1",
      "category": "memory_safety|buffer_overflow|input_validation|crypto|auth|other",
      "description": "Security issue description",
      "severity": "low|medium|high|critical",
      "likelihood": "low|medium|high",
      "impact": "low|medium|high|critical"
    }}
  ],
  "proposed_solutions": [
    {{
      "issue_id": "SEC-ISSUE-1",
      "summary": "Short fix summary",
      "steps": [
        "Concrete remediation step 1 (e.g., replace unsafe API X with safer alternative Y)",
        "Concrete remediation step 2"
      ],
      "implementation_hints": [
        "Hints for a coding agent (e.g., search for strcpy/strcat/sprintf and replace with bounded variants)"
      ]
    }}
  ],
  "architecture_assessment": {{
    "summary": "How the architecture affects the security posture",
    "observations": ["string"]
  }},
  "risks_and_concerns": [
    {{
      "description": "Risk if issues remain unaddressed",
      "likelihood": "low|medium|high",
      "impact": "low|medium|high|critical",
      "mitigations": ["string"]
    }}
  ],
  "recommendations": [
    {{
      "id": "SEC-REC-1",
      "priority": "P0|P1|P2",
      "description": "Security program recommendation (e.g., adopt secure coding standard, add CI security gate)",
      "related_issues": ["SEC-ISSUE-1"]
    }}
  ],
  "testing_plan": {{
    "overview": "Security testing strategy",
    "actions": [
      "Integrate ASan/UBSan into continuous integration for key binaries",
      "Introduce fuzz testing for parsers, protocol handlers, and other input-heavy components",
      "Add negative and abuse-case regression tests for critical security-sensitive APIs"
    ]
  }}
}}
""".strip()

    @staticmethod
    def get_intent_prompt_for_metrics(user_input_prompt: str) -> str:
        """
        Generate the system prompt for intent extraction related to code analysis
        and metrics queries (retrieve/compare/aggregate).

        This prompt remains focused on intent extraction and is unchanged in structure.
        """
        base_prompt = """
You are a senior C/C++ codebase analysis assistant focused on code health metrics and architecture.

Analyze the following user request and return a JSON object describing the user's intent
for code analysis and reporting.

You have access to detailed metrics such as:
- complexity (function/file-level metrics)
- maintainability (indices, hotspots)
- security (rule violations, severity)
- quality (banned APIs, style, hygiene)
- documentation (coverage, quality)
- test_coverage (tests, assertions, build integration)
- dependency (graph health, cycles, coupling)
- overall_health (combined scores and grades)

Supported intents:
- "retrieve": get metrics or summaries for specific modules/files/components.
- "compare": compare metrics between entities (modules, components, releases).
- "aggregate": summarize or aggregate metrics over the entire codebase.

Guidelines:
- For comparison, set "intent": "compare" and list "entities", each with its own criteria.
- For retrieval, set "intent": "retrieve" with a "criteria" object
  (e.g., {"module": "core.network", "metric": "security"}).
- For aggregation (e.g., "overall health score", "top 10 risky modules"),
  use "intent": "aggregate" and put the aggregation target in "criteria".

- If the request applies to ALL modules or the entire codebase, leave "criteria": {}.
- If a specific output format is requested (table, summary, JSON, list), include "output_format".
- If the user requests specific artifacts (e.g., "final report", "security summary",
  "modularization plan"), include them in "fields_to_extract".
- Always return a single JSON object, with no explanation or additional text.

Example 1:
User: "Compare security and complexity metrics for core.network and core.storage as a table."
Return:
{
  "intent": "compare",
  "entities": [
    {"module": "core.network"},
    {"module": "core.storage"}
  ],
  "fields_to_extract": ["security", "complexity"],
  "output_format": "table"
}

Example 2:
User: "Give me a summary of all health metrics for the networking module."
Return:
{
  "intent": "retrieve",
  "criteria": {"module": "network", "target": "health_metrics"},
  "fields_to_extract": ["overall_health", "critical_issues", "recommendations"],
  "output_format": "summary"
}

Example 3:
User: "Show the top 10 highest-risk modules based on combined scores."
Return:
{
  "intent": "aggregate",
  "criteria": {"target": "top_risk_modules", "limit": 10},
  "fields_to_extract": ["module", "overall_health", "security", "complexity"],
  "output_format": "table"
}

User request:
""".strip()

        return f"{base_prompt}\n{user_input_prompt}"