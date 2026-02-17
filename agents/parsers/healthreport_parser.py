import json
import hashlib
from typing import List, Dict, Any, Optional
from datetime import datetime

class HealthReportParser:
    def __init__(self):
        self.documents = []
        
    def parse_health_report(self, health_report: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Parse health report JSON into vector DB format"""
        self.documents = []
        
        # Extract metadata
        metadata_base = {
            "source": "health_report",
            "timestamp": health_report.get("metadata", {}).get("timestamp", ""),
            "project_name": health_report.get("metadata", {}).get("project_name", ""),
            "total_files": health_report.get("metadata", {}).get("total_files", 0)
        }
        
        # Parse different sections
        self._parse_metadata(health_report.get("metadata", {}), metadata_base)
        self._parse_health_metrics(health_report.get("health_metrics", {}), metadata_base)
        self._parse_llm_analysis(health_report.get("llm_analysis", {}), metadata_base)
        self._parse_llm_enhanced_report(health_report.get("llm_enhanced_report", {}), metadata_base)
        self._parse_errors(health_report.get("errors", []), metadata_base)
        
        return self.documents
    
    def _parse_metadata(self, metadata: Dict[str, Any], base_metadata: Dict[str, Any]):
        """Parse metadata section"""
        content = f"""
        Project Analysis Metadata:
        - Project: {metadata.get('project_name', 'Unknown')}
        - Total Files: {metadata.get('total_files', 0)}
        - Languages: {', '.join(metadata.get('languages', []))}
        - Analysis Date: {metadata.get('timestamp', 'Unknown')}
        - Analysis Duration: {metadata.get('analysis_duration_seconds', 0)} seconds
        """
        
        self._add_document(
            content=content.strip(),
            section="metadata",
            subsection="project_info",
            metadata=base_metadata
        )
    
    def _parse_health_metrics(self, health_metrics: Dict[str, Any], base_metadata: Dict[str, Any]):
        """Parse health metrics section"""
        
        # Overall health summary
        overall = health_metrics.get("overall_health", {})
        content = f"""
        Overall Health Score: {overall.get('score', 0)}/100 (Grade: {overall.get('grade', 'F')})
        
        Component Scores:
        """
        
        for contribution in overall.get("contributions", []):
            content += f"- {contribution['metric']}: {contribution['score']}/100 (Weight: {contribution['weight']})\n"
        
        content += f"\nCritical Issues:\n"
        for issue in overall.get("critical_issues", []):
            content += f"- {issue}\n"
            
        content += f"\nRecommendation: {overall.get('recommendation', 'No recommendation available')}"
        
        self._add_document(
            content=content.strip(),
            section="health_metrics",
            subsection="overall_summary",
            metadata={**base_metadata, "score": overall.get('score', 0)}
        )
        
        # Parse individual metric sections
        # Updated to include runtime_risk_score
        metric_sections = [
            "dependency_score", "quality_score", "complexity_score", 
            "maintainability_score", "documentation_score", 
            "test_coverage_score", "security_score", "runtime_risk_score"
        ]
        
        for section in metric_sections:
            if section in health_metrics:
                self._parse_metric_section(health_metrics[section], section, base_metadata)
    
    def _parse_metric_section(self, metric_data: Dict[str, Any], section_name: str, base_metadata: Dict[str, Any]):
        """Parse individual metric sections"""
        
        # Main score and summary
        score = metric_data.get("score", 0)
        grade = metric_data.get("grade", "F")
        
        content = f"{section_name.replace('_', ' ').title()}: {score}/100 (Grade: {grade})\n\n"
        
        # Add issues
        issues = metric_data.get("issues", [])
        if issues:
            content += "Key Issues:\n"
            for issue in issues[:10]:  # Limit to top 10 issues
                content += f"- {issue}\n"
        
        # Add metrics if available
        metrics = metric_data.get("metrics", {})
        if metrics:
            content += "\nMetrics:\n"
            for key, value in metrics.items():
                if isinstance(value, (int, float)):
                    content += f"- {key.replace('_', ' ').title()}: {value}\n"
        
        self._add_document(
            content=content.strip(),
            section="health_metrics",
            subsection=section_name,
            metadata={**base_metadata, "score": score, "grade": grade}
        )
        
        # Parse detailed violations for security
        if section_name == "security_score" and "violations_by_file" in metric_data.get("metrics", {}):
            self._parse_security_violations(metric_data["metrics"]["violations_by_file"], base_metadata)
    
    def _parse_security_violations(self, violations: Dict[str, List], base_metadata: Dict[str, Any]):
        """Parse security violations by file"""
        
        for file_path, file_violations in violations.items():
            for violation in file_violations:
                content = f"""
                Security Violation in {file_path}:
                - Rule: {violation.get('rule', 'Unknown')}
                - Severity: {violation.get('severity', 'Unknown')}
                - Description: {violation.get('description', 'No description')}
                - Line: {violation.get('line', 'Unknown')}
                - Code: {violation.get('snippet', 'No code snippet')}
                - Fix: {violation.get('remediation', 'No remediation provided')}
                """
                
                self._add_document(
                    content=content.strip(),
                    section="security_violations",
                    subsection="file_violation",
                    metadata={
                        **base_metadata,
                        "file_path": file_path,
                        "severity": violation.get('severity', 'unknown'),
                        "rule": violation.get('rule', 'unknown'),
                        "line": violation.get('line', 0)
                    }
                )
    
    def _parse_llm_analysis(self, llm_analysis: Dict[str, Any], base_metadata: Dict[str, Any]):
        """Parse LLM analysis section"""
        
        analysis_sections = [
            "critical_issues", "security_concerns", "quality_improvements",
            "complexity_management", "maintainability_strategy", 
            "documentation_gaps", "testing_strategy", "priority_roadmap"
        ]
        
        for section in analysis_sections:
            if section in llm_analysis and llm_analysis[section]:
                content = llm_analysis[section]
                
                self._add_document(
                    content=content,
                    section="llm_analysis",
                    subsection=section,
                    metadata=base_metadata
                )
    
    def _parse_llm_enhanced_report(self, enhanced_report: Dict[str, Any], base_metadata: Dict[str, Any]):
        """Parse LLM enhanced report section"""
        
        report_sections = [
            "executive_summary", "critical_issues", "strategic_recommendations",
            "implementation_roadmap", "resource_requirements", "risk_assessment",
            "success_metrics", "technology_modernization", "team_development",
            "monitoring_strategy"
        ]
        
        for section in report_sections:
            if section in enhanced_report and enhanced_report[section]:
                content = enhanced_report[section]
                
                self._add_document(
                    content=content,
                    section="enhanced_report",
                    subsection=section,
                    metadata=base_metadata
                )
    
    def _parse_errors(self, errors: List[Dict[str, Any]], base_metadata: Dict[str, Any]):
        """Parse errors section"""
        
        for error in errors:
            content = f"""
            Analysis Error:
            - Stage: {error.get('stage', 'Unknown')}
            - Error: {error.get('error', 'No error message')}
            """
            
            self._add_document(
                content=content.strip(),
                section="errors",
                subsection="analysis_error",
                metadata={
                    **base_metadata,
                    "error_stage": error.get('stage', 'unknown')
                }
            )
    
    def _add_document(self, content: str, section: str, subsection: str, metadata: Dict[str, Any]):
        """Add a document to the collection"""
        
        # Generate unique ID
        doc_id = hashlib.md5(f"{section}_{subsection}_{content[:100]}".encode()).hexdigest()
        
        document = {
            "id": doc_id,
            "content": content,
            "metadata": {
                **metadata,
                "section": section,
                "subsection": subsection,
                "content_length": len(content)
            }
        }
        
        self.documents.append(document)

# Usage example
def parse_health_report_file(file_path: str) -> List[Dict[str, Any]]:
    """Parse health report JSON file"""
    
    with open(file_path, 'r') as f:
        health_report = json.load(f)
    
    parser = HealthReportParser()
    documents = parser.parse_health_report(health_report)
    
    return documents

# Example usage
if __name__ == "__main__":
    # Parse the health report
    documents = parse_health_report_file("./out/parseddata/health_report_llm.json")
    
    print(f"Generated {len(documents)} documents for vector DB")
    
    # Show example documents
    for i, doc in enumerate(documents[:3]):
        print(f"\n--- Document {i+1} ---")
        print(f"ID: {doc['id']}")
        print(f"Section: {doc['metadata']['section']}")
        print(f"Subsection: {doc['metadata']['subsection']}")
        print(f"Content preview: {doc['content'][:200]}...")