"""
Generate Mermaid diagrams and visualizations for C/C++ analysis results
"""

import os
import re
from typing import Dict, List, Any, Optional
from pathlib import Path


class GraphGenerator:
    """
    Generates Mermaid diagrams and other visualizations for C/C++ codebase analysis results.
    
    Creates various types of diagrams including:
    - Dependency graphs
    - Architecture diagrams
    - Health metrics dashboards
    - Modularization plans
    """

    def __init__(self):
        """Initialize graph generator."""
        self.max_nodes = 50  # Limit nodes for readability
        self.max_edges = 100  # Limit edges for readability

    def generate_all_visualizations(
        self,
        dependency_graph: Dict[str, Any],
        modularization_plan: Dict[str, Any],
        health_metrics: Dict[str, Any],
        output_dir: str
    ) -> Dict[str, str]:
        """
        Generate all visualization files.
        
        Args:
            dependency_graph: Dependency analysis results
            modularization_plan: Modularization recommendations
            health_metrics: Health metrics analysis
            output_dir: Directory to save visualization files
            
        Returns:
            Dictionary mapping visualization names to file paths
        """
        os.makedirs(output_dir, exist_ok=True)
        generated_files = {}
        
        try:
            # Generate dependency graph
            dep_graph_path = self.generate_dependency_graph(dependency_graph, output_dir)
            if dep_graph_path:
                generated_files['dependency_graph'] = dep_graph_path
            
            # Generate health metrics dashboard
            health_dashboard_path = self.generate_health_dashboard(health_metrics, output_dir)
            if health_dashboard_path:
                generated_files['health_dashboard'] = health_dashboard_path
            
            # Generate modularization diagram
            mod_diagram_path = self.generate_modularization_diagram(
                dependency_graph, modularization_plan, output_dir
            )
            if mod_diagram_path:
                generated_files['modularization_diagram'] = mod_diagram_path
            
            # Generate architecture overview
            arch_overview_path = self.generate_architecture_overview(
                dependency_graph, health_metrics, output_dir
            )
            if arch_overview_path:
                generated_files['architecture_overview'] = arch_overview_path
            
            # Generate issues summary
            issues_summary_path = self.generate_issues_summary(health_metrics, output_dir)
            if issues_summary_path:
                generated_files['issues_summary'] = issues_summary_path
            
        except Exception as e:
            print(f"Error generating visualizations: {e}")
        
        return generated_files

    def generate_dependency_graph(
        self, 
        dependency_graph: Dict[str, Any], 
        output_dir: str
    ) -> Optional[str]:
        """Generate Mermaid dependency graph diagram."""
        try:
            if not dependency_graph or 'analysis' not in dependency_graph:
                return None
            
            # Filter to most important nodes to avoid clutter
            internal_modules = []
            external_modules = []
            
            for module_name, module_data in dependency_graph.items():
                if module_name == 'analysis':
                    continue
                
                if module_data.get('external', False):
                    external_modules.append((module_name, module_data))
                else:
                    internal_modules.append((module_name, module_data))
            
            # Limit nodes for readability
            internal_modules = internal_modules[:self.max_nodes//2]
            external_modules = external_modules[:self.max_nodes//2]
            
            mermaid_content = self._create_dependency_mermaid(
                internal_modules, external_modules, dependency_graph.get('analysis', {})
            )
            
            file_path = os.path.join(output_dir, "dependency_graph.md")
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(mermaid_content)
            
            return file_path
            
        except Exception as e:
            print(f"Error generating dependency graph: {e}")
            return None

    def _create_dependency_mermaid(
        self, 
        internal_modules: List[tuple], 
        external_modules: List[tuple],
        analysis: Dict[str, Any]
    ) -> str:
        """Create Mermaid diagram content for dependency graph."""
        content = ["# C/C++ Dependency Graph", "", "```mermaid", "graph TD"]
        
        # Add internal modules (blue)
        for module_name, module_data in internal_modules:
            safe_name = self._sanitize_node_name(module_name)
            display_name = module_name.split('.')[-1]  # Show only last part
            language = module_data.get('language', 'unknown')
            
            if language in ['c_header', 'cpp_header']:
                content.append(f'    {safe_name}["{display_name}<br/>(Header)"]')
                content.append(f'    {safe_name} --> {safe_name}')
                content.append(f'    class {safe_name} headerFile')
            else:
                content.append(f'    {safe_name}["{display_name}<br/>({language.upper()})"]')
                content.append(f'    class {safe_name} sourceFile')
        
        # Add external modules (red)
        for module_name, module_data in external_modules:
            safe_name = self._sanitize_node_name(module_name)
            display_name = module_name.replace('external.', '').replace('std.', 'std::')
            category = module_data.get('category', 'external')
            
            content.append(f'    {safe_name}["{display_name}<br/>({category})"]')
            content.append(f'    class {safe_name} externalDep')
        
        # Add dependencies (limit to avoid clutter)
        edge_count = 0
        for module_name, module_data in internal_modules:
            if edge_count >= self.max_edges:
                break
            
            safe_from = self._sanitize_node_name(module_name)
            dependencies = module_data.get('dependencies', [])
            
            for dep in dependencies[:5]:  # Limit dependencies per module
                if edge_count >= self.max_edges:
                    break
                
                safe_to = self._sanitize_node_name(dep)
                content.append(f'    {safe_from} --> {safe_to}')
                edge_count += 1
        
        # Add styling
        content.extend([
            "",
            "    classDef sourceFile fill:#e1f5fe,stroke:#01579b,stroke-width:2px",
            "    classDef headerFile fill:#f3e5f5,stroke:#4a148c,stroke-width:2px", 
            "    classDef externalDep fill:#ffebee,stroke:#b71c1c,stroke-width:2px",
            "```",
            "",
            "## Graph Statistics",
            f"- Total modules: {analysis.get('total_nodes', 0)}",
            f"- Internal modules: {analysis.get('internal_nodes', 0)}",
            f"- External dependencies: {analysis.get('external_nodes', 0)}",
            f"- Circular dependencies: {analysis.get('cycle_count', 0)}",
            f"- Max fan-out: {analysis.get('max_fan_out', 0)}",
        ])
        
        if analysis.get('has_cycles', False):
            content.extend([
                "",
                "⚠️ **Warning**: Circular dependencies detected! This can cause compilation issues and indicates architectural problems."
            ])
        
        return '\n'.join(content)

    def generate_health_dashboard(
        self, 
        health_metrics: Dict[str, Any], 
        output_dir: str
    ) -> Optional[str]:
        """Generate health metrics dashboard."""
        try:
            if not health_metrics:
                return None
            
            mermaid_content = self._create_health_dashboard_mermaid(health_metrics)
            
            file_path = os.path.join(output_dir, "health_dashboard.md")
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(mermaid_content)
            
            return file_path
            
        except Exception as e:
            print(f"Error generating health dashboard: {e}")
            return None

    def _create_health_dashboard_mermaid(self, health_metrics: Dict[str, Any]) -> str:
        """Create Mermaid diagram for health metrics dashboard."""
        content = ["# C/C++ Codebase Health Dashboard", ""]
        
        # Overall health score
        overall_health = health_metrics.get('overall_health', {})
        overall_score = overall_health.get('score', 0)
        overall_grade = overall_health.get('grade', 'F')
        
        content.extend([
            f"## Overall Health Score: {overall_score}/100 ({overall_grade})",
            "",
            "```mermaid",
            "graph LR"
        ])
        
        # Create health metrics nodes
        metrics_data = []
        for metric_name, metric_info in health_metrics.items():
            if isinstance(metric_info, dict) and 'score' in metric_info:
                score = metric_info.get('score', 0)
                grade = metric_info.get('grade', 'F')
                metrics_data.append((metric_name, score, grade))
        
        # Sort by score (worst first)
        metrics_data.sort(key=lambda x: x[1])
        
        for metric_name, score, grade in metrics_data:
            safe_name = self._sanitize_node_name(metric_name)
            display_name = metric_name.replace('_', ' ').title().replace(' Score', '')
            
            content.append(f'    {safe_name}["{display_name}<br/>{score}/100 ({grade})"]')
            
            # Color based on grade
            if grade in ['A', 'B']:
                content.append(f'    class {safe_name} good')
            elif grade == 'C':
                content.append(f'    class {safe_name} warning')
            else:
                content.append(f'    class {safe_name} critical')
        
        # Add overall health node
        content.append(f'    Overall["Overall Health<br/>{overall_score}/100 ({overall_grade})"]')
        
        # Connect metrics to overall
        for metric_name, _, _ in metrics_data:
            safe_name = self._sanitize_node_name(metric_name)
            content.append(f'    {safe_name} --> Overall')
        
        # Add styling
        content.extend([
            "",
            "    classDef good fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px",
            "    classDef warning fill:#fff3e0,stroke:#ef6c00,stroke-width:2px",
            "    classDef critical fill:#ffcdd2,stroke:#c62828,stroke-width:2px",
            "```",
            ""
        ])
        
        # Add detailed metrics table
        content.extend([
            "## Detailed Metrics",
            "",
            "| Metric | Score | Grade | Status |",
            "|--------|-------|-------|--------|"
        ])
        
        for metric_name, score, grade in metrics_data:
            display_name = metric_name.replace('_', ' ').title()
            status = "✅ Good" if grade in ['A', 'B'] else "⚠️ Warning" if grade == 'C' else "❌ Critical"
            content.append(f"| {display_name} | {score}/100 | {grade} | {status} |")
        
        # Add critical issues if available
        critical_issues = overall_health.get('critical_issues', [])
        if critical_issues:
            content.extend([
                "",
                "## Critical Issues",
                ""
            ])
            for i, issue in enumerate(critical_issues[:10], 1):
                content.append(f"{i}. {issue}")
        
        return '\n'.join(content)

    def generate_modularization_diagram(
        self,
        dependency_graph: Dict[str, Any],
        modularization_plan: Dict[str, Any],
        output_dir: str
    ) -> Optional[str]:
        """Generate modularization plan diagram."""
        try:
            if not modularization_plan:
                return None
            
            mermaid_content = self._create_modularization_mermaid(
                dependency_graph, modularization_plan
            )
            
            file_path = os.path.join(output_dir, "modularization_plan.md")
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(mermaid_content)
            
            return file_path
            
        except Exception as e:
            print(f"Error generating modularization diagram: {e}")
            return None

    def _create_modularization_mermaid(
        self,
        dependency_graph: Dict[str, Any],
        modularization_plan: Dict[str, Any]
    ) -> str:
        """Create Mermaid diagram for modularization plan."""
        content = ["# C/C++ Modularization Plan", "", "```mermaid", "graph TD"]
        
        # Get base plan
        base_plan = modularization_plan.get('base_plan', {})
        
        # Group modules by action
        actions = {}
        for module_name, plan_data in base_plan.items():
            action = plan_data.get('action', 'no_action')
            if action not in actions:
                actions[action] = []
            actions[action].append((module_name, plan_data))
        
        # Create nodes for each action group
        for action, modules in actions.items():
            safe_action = self._sanitize_node_name(action)
            display_action = action.replace('_', ' ').title()
            
            content.append(f'    {safe_action}["{display_action}<br/>({len(modules)} modules)"]')
            
            # Color based on action type
            if action in ['split', 'refactor']:
                content.append(f'    class {safe_action} highPriority')
            elif action in ['extract_interface', 'break_cycle']:
                content.append(f'    class {safe_action} mediumPriority')
            else:
                content.append(f'    class {safe_action} lowPriority')
            
            # Add individual modules (limit to avoid clutter)
            for i, (module_name, plan_data) in enumerate(modules[:5]):
                safe_module = self._sanitize_node_name(f"{action}_{module_name}")
                display_module = module_name.split('.')[-1]
                priority = plan_data.get('priority', 'low')
                
                content.append(f'    {safe_module}["{display_module}<br/>({priority})"]')
                content.append(f'    {safe_action} --> {safe_module}')
                
                if priority == 'critical':
                    content.append(f'    class {safe_module} critical')
                elif priority == 'high':
                    content.append(f'    class {safe_module} high')
                else:
                    content.append(f'    class {safe_module} normal')
        
        # Add styling
        content.extend([
            "",
            "    classDef highPriority fill:#ffcdd2,stroke:#c62828,stroke-width:3px",
            "    classDef mediumPriority fill:#fff3e0,stroke:#ef6c00,stroke-width:2px",
            "    classDef lowPriority fill:#e8f5e8,stroke:#2e7d32,stroke-width:2px",
            "    classDef critical fill:#d32f2f,stroke:#ffffff,stroke-width:2px,color:#ffffff",
            "    classDef high fill:#f57c00,stroke:#ffffff,stroke-width:2px,color:#ffffff",
            "    classDef normal fill:#388e3c,stroke:#ffffff,stroke-width:1px,color:#ffffff",
            "```",
            ""
        ])
        
        # Add action summary
        content.extend([
            "## Modularization Actions Summary",
            "",
            "| Action | Module Count | Priority | Description |",
            "|--------|--------------|----------|-------------|"
        ])
        
        action_descriptions = {
            'split': 'Break down large modules into smaller, focused components',
            'refactor': 'Restructure to resolve circular dependencies',
            'extract_interface': 'Create clean interfaces for highly coupled modules',
            'break_cycle': 'Resolve circular dependency issues',
            'consolidate': 'Merge small, related modules',
            'fix_includes': 'Fix missing or incorrect include statements'
        }
        
        for action, modules in actions.items():
            display_action = action.replace('_', ' ').title()
            avg_priority = self._calculate_average_priority(modules)
            description = action_descriptions.get(action, 'Improve module structure')
            
            content.append(f"| {display_action} | {len(modules)} | {avg_priority} | {description} |")
        
        # Add LLM recommendations if available
        llm_plan = modularization_plan.get('llm_enhanced_plan', {})
        if llm_plan:
            content.extend([
                "",
                "## LLM Enhanced Recommendations",
                ""
            ])
            
            for key, value in llm_plan.items():
                if isinstance(value, str) and value.strip():
                    section_title = key.replace('_', ' ').title()
                    content.append(f"### {section_title}")
                    content.append(value.strip())
                    content.append("")
        
        return '\n'.join(content)

    def generate_architecture_overview(
        self,
        dependency_graph: Dict[str, Any],
        health_metrics: Dict[str, Any],
        output_dir: str
    ) -> Optional[str]:
        """Generate architecture overview diagram."""
        try:
            mermaid_content = self._create_architecture_overview_mermaid(
                dependency_graph, health_metrics
            )
            
            file_path = os.path.join(output_dir, "architecture_overview.md")
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(mermaid_content)
            
            return file_path
            
        except Exception as e:
            print(f"Error generating architecture overview: {e}")
            return None

    def _create_architecture_overview_mermaid(
        self,
        dependency_graph: Dict[str, Any],
        health_metrics: Dict[str, Any]
    ) -> str:
        """Create Mermaid diagram for architecture overview."""
        content = ["# C/C++ Architecture Overview", ""]
        
        analysis = dependency_graph.get('analysis', {})
        
        # Architecture statistics
        content.extend([
            "## Architecture Statistics",
            "",
            f"- **Total Files**: {analysis.get('total_nodes', 0)}",
            f"- **Header Files**: {analysis.get('header_files', 0)}",
            f"- **Source Files**: {analysis.get('source_files', 0)}",
            f"- **External Dependencies**: {analysis.get('external_nodes', 0)}",
            f"- **Circular Dependencies**: {analysis.get('cycle_count', 0)}",
            "",
            "```mermaid",
            "graph TB"
        ])
        
        # Create layer-based architecture view
        header_count = analysis.get('header_files', 0)
        source_count = analysis.get('source_files', 0)
        external_count = analysis.get('external_nodes', 0)
        
        # Headers layer
        if header_count > 0:
            content.append(f'    Headers["Header Files<br/>({header_count} files)"]')
            content.append('    class Headers headerLayer')
        
        # Source layer
        if source_count > 0:
            content.append(f'    Sources["Source Files<br/>({source_count} files)"]')
            content.append('    class Sources sourceLayer')
        
        # External dependencies
        if external_count > 0:
            content.append(f'    External["External Dependencies<br/>({external_count} deps)"]')
            content.append('    class External externalLayer')
        
        # Add relationships
        if header_count > 0 and source_count > 0:
            content.append('    Headers --> Sources')
        
        if external_count > 0:
            if header_count > 0:
                content.append('    External --> Headers')
            if source_count > 0:
                content.append('    External --> Sources')
        
        # Add health indicator
        overall_health = health_metrics.get('overall_health', {})
        health_score = overall_health.get('score', 0)
        health_grade = overall_health.get('grade', 'F')
        
        content.append(f'    Health["Health Score<br/>{health_score}/100 ({health_grade})"]')
        
        if health_grade in ['A', 'B']:
            content.append('    class Health healthGood')
        elif health_grade == 'C':
            content.append('    class Health healthWarning')
        else:
            content.append('    class Health healthCritical')
        
        # Add styling
        content.extend([
            "",
            "    classDef headerLayer fill:#e1f5fe,stroke:#01579b,stroke-width:2px",
            "    classDef sourceLayer fill:#f3e5f5,stroke:#4a148c,stroke-width:2px",
            "    classDef externalLayer fill:#ffebee,stroke:#b71c1c,stroke-width:2px",
            "    classDef healthGood fill:#c8e6c9,stroke:#2e7d32,stroke-width:3px",
            "    classDef healthWarning fill:#fff3e0,stroke:#ef6c00,stroke-width:3px",
            "    classDef healthCritical fill:#ffcdd2,stroke:#c62828,stroke-width:3px",
            "```",
            ""
        ])
        
        # Add quality indicators
        content.extend([
            "## Quality Indicators",
            ""
        ])
        
        # Circular dependencies warning
        if analysis.get('has_cycles', False):
            cycle_count = analysis.get('cycle_count', 0)
            content.append(f"❌ **Circular Dependencies**: {cycle_count} cycles detected")
        else:
            content.append("✅ **No Circular Dependencies**: Clean dependency structure")
        
        # Coupling analysis
        max_fan_out = analysis.get('max_fan_out', 0)
        if max_fan_out > 15:
            content.append(f"⚠️ **High Coupling**: Maximum fan-out of {max_fan_out}")
        elif max_fan_out > 10:
            content.append(f"⚠️ **Moderate Coupling**: Maximum fan-out of {max_fan_out}")
        else:
            content.append(f"✅ **Good Coupling**: Maximum fan-out of {max_fan_out}")
        
        # Header-to-source ratio
        header_to_source_ratio = analysis.get('header_to_source_ratio', 0)
        if header_to_source_ratio > 3:
            content.append(f"⚠️ **High Header Ratio**: {header_to_source_ratio:.1f}:1 (consider consolidation)")
        elif header_to_source_ratio < 0.5:
            content.append(f"⚠️ **Low Header Ratio**: {header_to_source_ratio:.1f}:1 (missing headers?)")
        else:
            content.append(f"✅ **Balanced Header Ratio**: {header_to_source_ratio:.1f}:1")
        
        return '\n'.join(content)

    def generate_issues_summary(
        self,
        health_metrics: Dict[str, Any],
        output_dir: str
    ) -> Optional[str]:
        """Generate issues summary visualization."""
        try:
            mermaid_content = self._create_issues_summary_mermaid(health_metrics)
            
            file_path = os.path.join(output_dir, "issues_summary.md")
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(mermaid_content)
            
            return file_path
            
        except Exception as e:
            print(f"Error generating issues summary: {e}")
            return None

    def _create_issues_summary_mermaid(self, health_metrics: Dict[str, Any]) -> str:
        """Create Mermaid diagram for issues summary."""
        content = ["# C/C++ Issues Summary", ""]
        
        # Collect all issues
        all_issues = []
        for metric_name, metric_data in health_metrics.items():
            if isinstance(metric_data, dict) and 'issues' in metric_data:
                issues = metric_data.get('issues', [])
                for issue in issues:
                    all_issues.append({
                        'category': metric_name.replace('_score', '').replace('_', ' ').title(),
                        'description': issue,
                        'severity': 'high' if any(word in issue.lower() for word in ['critical', 'security', 'vulnerability']) else 'medium'
                    })
        
        # Get critical issues from overall health
        overall_health = health_metrics.get('overall_health', {})
        critical_issues = overall_health.get('critical_issues', [])
        
        content.extend([
            f"## Total Issues: {len(all_issues)}",
            f"## Critical Issues: {len(critical_issues)}",
            ""
        ])
        
        if critical_issues:
            content.extend([
                "### Critical Issues Requiring Immediate Attention",
                ""
            ])
            for i, issue in enumerate(critical_issues[:10], 1):
                content.append(f"{i}. ❌ {issue}")
            content.append("")
        
        # Create issues by category
        issues_by_category = {}
        for issue in all_issues:
            category = issue['category']
            if category not in issues_by_category:
                issues_by_category[category] = []
            issues_by_category[category].append(issue)
        
        if issues_by_category:
            content.extend([
                "```mermaid",
                "graph LR"
            ])
            
            # Create category nodes
            for category, issues in issues_by_category.items():
                safe_category = self._sanitize_node_name(category)
                issue_count = len(issues)
                critical_count = len([i for i in issues if i['severity'] == 'high'])
                
                content.append(f'    {safe_category}["{category}<br/>{issue_count} issues<br/>{critical_count} critical"]')
                
                if critical_count > 0:
                    content.append(f'    class {safe_category} critical')
                elif issue_count > 5:
                    content.append(f'    class {safe_category} warning')
                else:
                    content.append(f'    class {safe_category} normal')
            
            # Add central issues node
            content.append(f'    Issues["Total Issues<br/>{len(all_issues)}"]')
            
            # Connect categories to central node
            for category in issues_by_category.keys():
                safe_category = self._sanitize_node_name(category)
                content.append(f'    {safe_category} --> Issues')
            
            # Add styling
            content.extend([
                "",
                "    classDef critical fill:#ffcdd2,stroke:#c62828,stroke-width:3px",
                "    classDef warning fill:#fff3e0,stroke:#ef6c00,stroke-width:2px",
                "    classDef normal fill:#e8f5e8,stroke:#2e7d32,stroke-width:2px",
                "```",
                ""
            ])
        
        # Add detailed issues by category
        for category, issues in issues_by_category.items():
            content.extend([
                f"### {category} Issues ({len(issues)})",
                ""
            ])
            
            for i, issue in enumerate(issues[:10], 1):  # Limit to top 10 per category
                severity_icon = "❌" if issue['severity'] == 'high' else "⚠️"
                content.append(f"{i}. {severity_icon} {issue['description']}")
            
            if len(issues) > 10:
                content.append(f"... and {len(issues) - 10} more issues")
            
            content.append("")
        
        return '\n'.join(content)

    def _sanitize_node_name(self, name: str) -> str:
        """Sanitize node name for Mermaid compatibility."""
        # Replace problematic characters
        sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', name)
        # Ensure it starts with a letter
        if sanitized and not sanitized[0].isalpha():
            sanitized = 'node_' + sanitized
        return sanitized or 'unknown'

    def _calculate_average_priority(self, modules: List[tuple]) -> str:
        """Calculate average priority for a list of modules."""
        priority_values = {'critical': 4, 'high': 3, 'medium': 2, 'low': 1}
        
        total_value = 0
        count = 0
        
        for _, plan_data in modules:
            priority = plan_data.get('priority', 'low')
            total_value += priority_values.get(priority, 1)
            count += 1
        
        if count == 0:
            return 'low'
        
        avg_value = total_value / count
        
        if avg_value >= 3.5:
            return 'critical'
        elif avg_value >= 2.5:
            return 'high'
        elif avg_value >= 1.5:
            return 'medium'
        else:
            return 'low'