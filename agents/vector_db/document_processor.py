# agents/vector_db/document_processor.py
import json
import os
import re
from typing import Dict, List, Any, Optional
from pathlib import Path
from datetime import datetime

class VectorDBDocumentProcessor:
    """
    Process analysis results into documents optimized for vector database storage
    and chatbot interactions.
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.chunk_size = config.get('chunk_size', 4000)
        self.overlap_size = config.get('overlap_size', 200)
        self.include_code = config.get('include_code', True)
        self.cpp_specific = config.get('cpp_specific', True)
        self.enable_health_reports = config.get('enable_health_reports', True)
        self.enable_chatbot_optimization = config.get('enable_chatbot_optimization', True)
    
    def prepare_documents(self, file_cache: List[Dict[str, Any]], 
                         dependency_graph: Dict[str, Any] = None,
                         health_metrics: Dict[str, Any] = None,
                         summary: Dict[str, Any] = None,
                         health_report: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        Prepare documents from analysis results for vector database ingestion.
        """
        documents = []
        
        # Process individual file documents
        if file_cache:
            documents.extend(self._process_cpp_file_documents(file_cache))
        
        # Process dependency graph documents
        if dependency_graph:
            documents.extend(self._process_dependency_documents(dependency_graph))
        
        # Process health metrics documents
        if health_metrics:
            documents.extend(self._process_health_documents(health_metrics))
        
        # Process summary documents
        if summary:
            documents.extend(self._process_summary_documents(summary))
        
        # Process full health report if available
        if health_report and self.enable_health_reports:
            documents.extend(self._process_health_report_documents(health_report))
        
        # Add chatbot optimization metadata if enabled
        if self.enable_chatbot_optimization:
            documents = self._add_chatbot_metadata(documents)
        
        return documents
    
    def _process_cpp_file_documents(self, file_cache: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process individual C/C++ file documents."""
        documents = []
        
        for file_data in file_cache:
            if not isinstance(file_data, dict):
                continue
                
            # Create main file document
            file_doc = self._create_cpp_file_document(file_data)
            if file_doc:
                documents.append(file_doc)
            
            # Create function-specific documents
            functions = file_data.get('functions', [])
            if isinstance(functions, list):
                for func in functions:
                    if isinstance(func, dict):
                        func_doc = self._create_function_document(file_data, func)
                        if func_doc:
                            documents.append(func_doc)
            
            # Create class-specific documents
            classes = file_data.get('classes', [])
            if isinstance(classes, list):
                for cls in classes:
                    if isinstance(cls, dict):
                        class_doc = self._create_class_document(file_data, cls)
                        if class_doc:
                            documents.append(class_doc)
        
        return documents
    
    def _create_cpp_file_document(self, file_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Create a document for a C/C++ file."""
        try:
            file_path = file_data.get('file_relative_path', file_data.get('file_name', 'unknown'))
            file_name = file_data.get('file_name', Path(file_path).name)
            
            # Build content sections
            content_sections = []
            
            # File overview
            overview = f"File: {file_name}\n"
            overview += f"Path: {file_path}\n"
            overview += f"Language: {file_data.get('language', 'cpp')}\n"
            overview += f"Size: {file_data.get('size_bytes', 0)} bytes\n"
            
            # Metrics
            metrics = file_data.get('metrics', {})
            if metrics:
                overview += f"Lines: {metrics.get('total_lines', 0)} total, {metrics.get('code_lines', 0)} code\n"
                overview += f"Comments: {metrics.get('comment_lines', 0)} lines ({metrics.get('comment_ratio', 0):.1%})\n"
            
            content_sections.append(overview)
            
            # Analysis results
            analysis_section = self._build_analysis_section(file_data)
            if analysis_section:
                content_sections.append(analysis_section)
            
            # Dependencies
            dependencies = file_data.get('dependencies', [])
            if dependencies:
                deps_section = "Dependencies:\n" + "\n".join(f"- {dep}" for dep in dependencies)
                content_sections.append(deps_section)
            
            # Functions summary
            functions = file_data.get('functions', [])
            if functions:
                func_section = f"Functions ({len(functions)}):\n"
                for func in functions[:10]:  # Limit to first 10
                    if isinstance(func, dict):
                        func_section += f"- {func.get('name', 'unknown')} (line {func.get('line', 0)})\n"
                content_sections.append(func_section)
            
            # Classes summary
            classes = file_data.get('classes', [])
            if classes:
                class_section = f"Classes ({len(classes)}):\n"
                for cls in classes[:10]:  # Limit to first 10
                    if isinstance(cls, dict):
                        class_section += f"- {cls.get('name', 'unknown')} (line {cls.get('line', 0)})\n"
                content_sections.append(class_section)
            
            # Include source code if enabled and available
            if self.include_code and file_data.get('source'):
                source_code = file_data['source']
                if len(source_code) <= self.chunk_size:
                    content_sections.append(f"Source Code:\n```cpp\n{source_code}\n```")
                else:
                    # Truncate large source files
                    truncated = source_code[:self.chunk_size - 200]
                    content_sections.append(f"Source Code (truncated):\n```cpp\n{truncated}\n...\n```")
            
            # Combine all sections
            content = "\n\n".join(content_sections)
            
            # Create document
            document = {
                'id': f"file_{file_path.replace('/', '_').replace('.', '_')}",
                'content': content,
                'metadata': {
                    'type': 'cpp_file',
                    'file_path': file_path,
                    'file_name': file_name,
                    'language': file_data.get('language', 'cpp'),
                    'size_bytes': file_data.get('size_bytes', 0),
                    'metrics': metrics,
                    'complexity_score': file_data.get('complexity_score', 0),
                    'documentation_score': file_data.get('documentation_score', 0),
                    'security_issues_count': len(file_data.get('security_issues', [])),
                    'functions_count': len(functions),
                    'classes_count': len(classes),
                    'dependencies_count': len(dependencies),
                    'timestamp': datetime.now().isoformat()
                }
            }
            
            return document
            
        except Exception as e:
            print(f"Error creating file document for {file_data.get('file_name', 'unknown')}: {e}")
            return None
    
    def _create_function_document(self, file_data: Dict[str, Any], func_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Create a document for a specific function."""
        try:
            file_path = file_data.get('file_relative_path', file_data.get('file_name', 'unknown'))
            func_name = func_data.get('name', 'unknown')
            
            content_sections = []
            
            # Function overview
            overview = f"Function: {func_name}\n"
            overview += f"File: {file_path}\n"
            overview += f"Line: {func_data.get('line', 0)}\n"
            overview += f"Type: {func_data.get('type', 'function')}\n"
            overview += f"Complexity: {func_data.get('cyclomatic_complexity', 1)}\n"
            
            content_sections.append(overview)
            
            # Function-specific analysis
            if func_data.get('parameters'):
                params_section = "Parameters:\n" + "\n".join(f"- {param}" for param in func_data['parameters'])
                content_sections.append(params_section)
            
            if func_data.get('calls'):
                calls_section = "Function Calls:\n" + "\n".join(f"- {call}" for call in func_data['calls'])
                content_sections.append(calls_section)
            
            # Combine sections
            content = "\n\n".join(content_sections)
            
            document = {
                'id': f"function_{file_path.replace('/', '_').replace('.', '_')}_{func_name}",
                'content': content,
                'metadata': {
                    'type': 'cpp_function',
                    'file_path': file_path,
                    'function_name': func_name,
                    'line_number': func_data.get('line', 0),
                    'complexity': func_data.get('cyclomatic_complexity', 1),
                    'timestamp': datetime.now().isoformat()
                }
            }
            
            return document
            
        except Exception as e:
            print(f"Error creating function document for {func_data.get('name', 'unknown')}: {e}")
            return None
    
    def _create_class_document(self, file_data: Dict[str, Any], class_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Create a document for a specific class."""
        try:
            file_path = file_data.get('file_relative_path', file_data.get('file_name', 'unknown'))
            class_name = class_data.get('name', 'unknown')
            
            content_sections = []
            
            # Class overview
            overview = f"Class: {class_name}\n"
            overview += f"File: {file_path}\n"
            overview += f"Line: {class_data.get('line', 0)}\n"
            overview += f"Type: {class_data.get('type', 'class')}\n"
            
            content_sections.append(overview)
            
            # Class-specific analysis
            if class_data.get('methods'):
                methods_section = "Methods:\n" + "\n".join(f"- {method}" for method in class_data['methods'])
                content_sections.append(methods_section)
            
            if class_data.get('members'):
                members_section = "Members:\n" + "\n".join(f"- {member}" for member in class_data['members'])
                content_sections.append(members_section)
            
            # Combine sections
            content = "\n\n".join(content_sections)
            
            document = {
                'id': f"class_{file_path.replace('/', '_').replace('.', '_')}_{class_name}",
                'content': content,
                'metadata': {
                    'type': 'cpp_class',
                    'file_path': file_path,
                    'class_name': class_name,
                    'line_number': class_data.get('line', 0),
                    'timestamp': datetime.now().isoformat()
                }
            }
            
            return document
            
        except Exception as e:
            print(f"Error creating class document for {class_data.get('name', 'unknown')}: {e}")
            return None
    
    def _build_analysis_section(self, file_data: Dict[str, Any]) -> str:
        """Build analysis results section for a file."""
        sections = []
        
        # Complexity analysis
        complexity_score = file_data.get('complexity_score', 0)
        if complexity_score > 0:
            sections.append(f"Complexity Score: {complexity_score:.1f}/100")
        
        # Documentation analysis
        doc_score = file_data.get('documentation_score', 0)
        if doc_score > 0:
            sections.append(f"Documentation Score: {doc_score:.1f}/100")
        
        # Security issues
        security_issues = file_data.get('security_issues', [])
        if security_issues:
            sections.append(f"Security Issues: {len(security_issues)} found")
            for issue in security_issues[:3]:  # Show first 3 issues
                if isinstance(issue, dict):
                    sections.append(f"- {issue.get('description', 'Unknown issue')} (line {issue.get('line', 0)})")
        
        # Quality metrics
        quality_metrics = file_data.get('code_quality_metrics', {})
        if quality_metrics:
            sections.append("Quality Metrics:")
            for key, value in quality_metrics.items():
                if isinstance(value, (int, float)):
                    sections.append(f"- {key}: {value}")
        
        return "\n".join(sections) if sections else ""
    
    def _process_dependency_documents(self, dependency_graph: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Process dependency graph into documents."""
        documents = []
        
        try:
            # Create overall dependency summary
            total_files = len(dependency_graph)
            total_deps = sum(len(deps) if isinstance(deps, list) else 0 for deps in dependency_graph.values())
            
            summary_content = f"Dependency Analysis Summary\n\n"
            summary_content += f"Total Files: {total_files}\n"
            summary_content += f"Total Dependencies: {total_deps}\n"
            summary_content += f"Average Dependencies per File: {total_deps/total_files:.1f}\n\n"
            
            # Top files by dependency count
            dep_counts = [(file, len(deps) if isinstance(deps, list) else 0) 
                         for file, deps in dependency_graph.items()]
            dep_counts.sort(key=lambda x: x[1], reverse=True)
            
            summary_content += "Files with Most Dependencies:\n"
            for file, count in dep_counts[:10]:
                summary_content += f"- {file}: {count} dependencies\n"
            
            documents.append({
                'id': 'dependency_summary',
                'content': summary_content,
                'metadata': {
                    'type': 'dependency_summary',
                    'total_files': total_files,
                    'total_dependencies': total_deps,
                    'timestamp': datetime.now().isoformat()
                }
            })
            
            # Create individual file dependency documents
            for file_path, deps in dependency_graph.items():
                if not isinstance(deps, list) or not deps:
                    continue
                    
                content = f"Dependencies for {file_path}\n\n"
                content += f"Total Dependencies: {len(deps)}\n\n"
                content += "Included Files:\n"
                for dep in deps:
                    content += f"- {dep}\n"
                
                documents.append({
                    'id': f"deps_{file_path.replace('/', '_').replace('.', '_')}",
                    'content': content,
                    'metadata': {
                        'type': 'file_dependencies',
                        'file_path': file_path,
                        'dependency_count': len(deps),
                        'timestamp': datetime.now().isoformat()
                    }
                })
        
        except Exception as e:
            print(f"Error processing dependency documents: {e}")
        
        return documents
    
    def _process_health_documents(self, health_metrics: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Process health metrics into documents."""
        documents = []
        
        try:
            # Overall health summary
            overall_health = health_metrics.get('overall_health', {})
            content = "Project Health Summary\n\n"
            
            if overall_health:
                content += f"Overall Score: {overall_health.get('score', 0):.1f}/100\n"
                content += f"Overall Grade: {overall_health.get('grade', 'F')}\n\n"
            
            # Individual metric scores
            metrics = [
                ('complexity_score', 'Code Complexity'),
                ('documentation_score', 'Documentation Quality'),
                ('security_score', 'Security Analysis'),
                ('quality_score', 'Code Quality'),
                ('maintainability_score', 'Maintainability'),
                ('dependency_score', 'Dependency Management')
            ]
            
            content += "Detailed Scores:\n"
            for metric_key, metric_name in metrics:
                metric_data = health_metrics.get(metric_key, {})
                if isinstance(metric_data, dict):
                    score = metric_data.get('score', 0)
                    grade = metric_data.get('grade', 'F')
                    content += f"- {metric_name}: {score:.1f}/100 ({grade})\n"
            
            # Statistics
            stats = health_metrics.get('statistics', {})
            if stats:
                content += f"\nProject Statistics:\n"
                content += f"- Total Files: {stats.get('total_files', 0)}\n"
                content += f"- Total Lines: {stats.get('total_lines', 0):,}\n"
                content += f"- Total Functions: {stats.get('total_functions', 0):,}\n"
                content += f"- Total Classes: {stats.get('total_classes', 0):,}\n"
            
            documents.append({
                'id': 'health_summary',
                'content': content,
                'metadata': {
                    'type': 'health_summary',
                    'overall_score': overall_health.get('score', 0) if overall_health else 0,
                    'overall_grade': overall_health.get('grade', 'F') if overall_health else 'F',
                    'timestamp': datetime.now().isoformat()
                }
            })
        
        except Exception as e:
            print(f"Error processing health documents: {e}")
        
        return documents
    
    def _process_summary_documents(self, summary: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Process analysis summary into documents."""
        documents = []
        
        try:
            content = "Codebase Analysis Summary\n\n"
            
            # File statistics
            file_stats = summary.get('file_stats', {})
            if file_stats:
                content += "File Statistics:\n"
                content += f"- Total Files: {file_stats.get('total_files', 0)}\n"
                content += f"- Processed Files: {file_stats.get('processed_files', 0)}\n"
                content += f"- Skipped Files: {file_stats.get('skipped_files', 0)}\n"
                content += f"- Error Files: {file_stats.get('error_files', 0)}\n"
                
                lang_dist = file_stats.get('language_distribution', {})
                if lang_dist:
                    content += "\nLanguage Distribution:\n"
                    for lang, count in lang_dist.items():
                        content += f"- {lang}: {count} files\n"
            
            # Code metrics
            code_metrics = summary.get('code_metrics', {})
            if code_metrics:
                content += f"\nCode Metrics:\n"
                content += f"- Total Lines: {code_metrics.get('total_lines', 0):,}\n"
                content += f"- Total Functions: {code_metrics.get('total_functions', 0):,}\n"
                content += f"- Total Classes: {code_metrics.get('total_classes', 0):,}\n"
            
            # Security analysis
            security_analysis = summary.get('security_analysis', {})
            if security_analysis:
                content += f"\nSecurity Analysis:\n"
                content += f"- Total Issues: {security_analysis.get('total_issues', 0)}\n"
                
                issues_by_severity = security_analysis.get('issues_by_severity', {})
                if issues_by_severity:
                    for severity, count in issues_by_severity.items():
                        content += f"- {severity.title()} Issues: {count}\n"
            
            documents.append({
                'id': 'analysis_summary',
                'content': content,
                'metadata': {
                    'type': 'analysis_summary',
                    'timestamp': datetime.now().isoformat()
                }
            })
        
        except Exception as e:
            print(f"Error processing summary documents: {e}")
        
        return documents
    
    def _process_health_report_documents(self, health_report: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Process full health report into documents."""
        documents = []
        
        try:
            # Create a comprehensive health report document
            content = "Comprehensive Health Report\n\n"
            
            # Metadata
            metadata = health_report.get('metadata', {})
            if metadata:
                content += f"Project: {metadata.get('project_name', 'Unknown')}\n"
                content += f"Analysis Time: {metadata.get('analysis_time', 0):.1f} seconds\n"
                content += f"Timestamp: {metadata.get('timestamp', 'Unknown')}\n"
                content += f"Analyzer Version: {metadata.get('analyzer_version', 'Unknown')}\n\n"
            
            # Health metrics summary
            health_metrics = health_report.get('health_metrics', {})
            if health_metrics:
                overall = health_metrics.get('overall_health', {})
                if overall:
                    content += f"Overall Health: {overall.get('score', 0):.1f}/100 ({overall.get('grade', 'F')})\n\n"
            
            # Key findings
            content += "Key Findings:\n"
            
            # Add security issues summary
            security_score = health_metrics.get('security_score', {})
            if isinstance(security_score, dict):
                critical = security_score.get('critical_issues', 0)
                high = security_score.get('high_issues', 0)
                total = security_score.get('total_issues', 0)
                content += f"- Security: {critical} critical, {high} high priority issues ({total} total)\n"
            
            # Add complexity summary
            complexity_score = health_metrics.get('complexity_score', {})
            if isinstance(complexity_score, dict):
                content += f"- Complexity: {complexity_score.get('score', 0):.1f}/100\n"
            
            # Add documentation summary
            doc_score = health_metrics.get('documentation_score', {})
            if isinstance(doc_score, dict):
                content += f"- Documentation: {doc_score.get('score', 0):.1f}/100\n"
            
            documents.append({
                'id': 'comprehensive_health_report',
                'content': content,
                'metadata': {
                    'type': 'comprehensive_health_report',
                    'timestamp': datetime.now().isoformat()
                }
            })
        
        except Exception as e:
            print(f"Error processing health report documents: {e}")
        
        return documents
    
    def _add_chatbot_metadata(self, documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Add chatbot-specific metadata to documents."""
        for doc in documents:
            if 'metadata' not in doc:
                doc['metadata'] = {}
            
            # Add chatbot-friendly tags based on document type
            doc_type = doc['metadata'].get('type', 'unknown')
            
            if doc_type == 'cpp_file':
                doc['metadata']['chatbot_tags'] = ['file', 'code', 'cpp', 'source']
                doc['metadata']['chatbot_summary'] = f"C++ file analysis for {doc['metadata'].get('file_name', 'unknown file')}"
            elif doc_type == 'cpp_function':
                doc['metadata']['chatbot_tags'] = ['function', 'code', 'cpp', 'method']
                doc['metadata']['chatbot_summary'] = f"Function {doc['metadata'].get('function_name', 'unknown')} analysis"
            elif doc_type == 'cpp_class':
                doc['metadata']['chatbot_tags'] = ['class', 'code', 'cpp', 'object']
                doc['metadata']['chatbot_summary'] = f"Class {doc['metadata'].get('class_name', 'unknown')} analysis"
            elif doc_type == 'health_summary':
                doc['metadata']['chatbot_tags'] = ['health', 'metrics', 'summary', 'analysis']
                doc['metadata']['chatbot_summary'] = "Project health and quality metrics summary"
            elif doc_type == 'dependency_summary':
                doc['metadata']['chatbot_tags'] = ['dependencies', 'includes', 'structure', 'architecture']
                doc['metadata']['chatbot_summary'] = "Project dependency analysis and structure"
            elif doc_type == 'analysis_summary':
                doc['metadata']['chatbot_tags'] = ['summary', 'overview', 'statistics', 'analysis']
                doc['metadata']['chatbot_summary'] = "Overall codebase analysis summary"
            
            # Add searchable keywords
            content_lower = doc.get('content', '').lower()
            keywords = []
            
            # Extract technical keywords
            cpp_keywords = ['class', 'function', 'method', 'variable', 'include', 'namespace', 'template']
            for keyword in cpp_keywords:
                if keyword in content_lower:
                    keywords.append(keyword)
            
            # Extract quality keywords
            quality_keywords = ['complexity', 'security', 'documentation', 'maintainability', 'quality']
            for keyword in quality_keywords:
                if keyword in content_lower:
                    keywords.append(keyword)
            
            doc['metadata']['chatbot_keywords'] = keywords
            
            # Add relevance scoring hints
            doc['metadata']['chatbot_relevance_hints'] = {
                'code_analysis': doc_type in ['cpp_file', 'cpp_function', 'cpp_class'],
                'health_metrics': doc_type in ['health_summary', 'comprehensive_health_report'],
                'project_overview': doc_type in ['analysis_summary', 'dependency_summary'],
                'technical_details': 'line_number' in doc['metadata'] or 'complexity' in doc['metadata']
            }
        
        return documents
    
    def save_documents_for_vectordb(self, documents: List[Dict[str, Any]], 
                                   output_dir: str, 
                                   include_chatbot_metadata: bool = True) -> str:
        """Save processed documents to files for vector database ingestion."""
        os.makedirs(output_dir, exist_ok=True)
        
        # Save main documents file
        documents_file = os.path.join(output_dir, 'vector_documents.json')
        with open(documents_file, 'w', encoding='utf-8') as f:
            json.dump(documents, f, indent=2, ensure_ascii=False)
        
        # Save metadata summary
        metadata_summary = {
            'total_documents': len(documents),
            'document_types': {},
            'generation_timestamp': datetime.now().isoformat(),
            'config': self.config
        }
        
        # Count document types
        for doc in documents:
            doc_type = doc.get('metadata', {}).get('type', 'unknown')
            metadata_summary['document_types'][doc_type] = metadata_summary['document_types'].get(doc_type, 0) + 1
        
        metadata_file = os.path.join(output_dir, 'documents_metadata.json')
        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(metadata_summary, f, indent=2, ensure_ascii=False)
        
        # Save chatbot-optimized documents if enabled
        if include_chatbot_metadata:
            chatbot_docs = []
            for doc in documents:
                chatbot_doc = {
                    'id': doc['id'],
                    'content': doc['content'],
                    'summary': doc.get('metadata', {}).get('chatbot_summary', ''),
                    'tags': doc.get('metadata', {}).get('chatbot_tags', []),
                    'keywords': doc.get('metadata', {}).get('chatbot_keywords', []),
                    'relevance_hints': doc.get('metadata', {}).get('chatbot_relevance_hints', {}),
                    'metadata': doc.get('metadata', {})
                }
                chatbot_docs.append(chatbot_doc)
            
            chatbot_file = os.path.join(output_dir, 'chatbot_documents.json')
            with open(chatbot_file, 'w', encoding='utf-8') as f:
                json.dump(chatbot_docs, f, indent=2, ensure_ascii=False)
        
        print(f"Saved {len(documents)} documents to {output_dir}")
        print(f"Document types: {metadata_summary['document_types']}")
        
        return documents_file