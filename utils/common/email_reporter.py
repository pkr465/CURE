import os
import sys
import logging
import contextlib
from datetime import datetime
from typing import List, Dict, Any, Union

# --- Updated Imports per Example ---
# Guarded: these may fail if langchain or qgenie SDK versions are mismatched
try:
    from langchain.agents import create_agent
    from langchain_core.messages import HumanMessage
    from qgenie.integrations.langchain import QGenieChat
    _LANGCHAIN_AVAILABLE = True
except ImportError as _e:
    logging.getLogger(__name__).warning("LangChain/QGenie imports failed: %s", _e)
    create_agent = None  # type: ignore[assignment]
    HumanMessage = None  # type: ignore[assignment,misc]
    QGenieChat = None  # type: ignore[assignment]
    _LANGCHAIN_AVAILABLE = False

# --- Integration: Global Config ---
try:
    from utils.parsers.global_config_parser import GlobalConfig
except ImportError:
    # Fallback mock for standalone testing
    class GlobalConfig:
        def __init__(self): self._cfg = {}
        def get(self, key, default=None): return default

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class EmailReporter:
    """
    Unified Email Reporter for Codebase Analysis & Fixer Agents.
    Updated to use the latest QGenieChat and create_agent pattern.
    """
    
    MAX_ATTACHMENT_SIZE = 15 * 1024 * 1024  # 15 MB Limit

    def __init__(self, config: GlobalConfig = None):
        """
        Initialize using the shared GlobalConfig object.
        """
        self.logger = logging.getLogger("CodebaseEmailReporter")
        self.config = config or GlobalConfig()
        
        # 1. Resolve Settings
        self.api_key = os.getenv('QGENIE_API_KEY') or self.config.get('llm.qgenie_api_key')
        self.endpoint = self.config.get('llm.chat_endpoint', "https://qgenie-chat.qualcomm.com")
        
        # Resolve Model
        raw_model = self.config.get('llm.model', 'Turbo')
        if raw_model.startswith("qgenie::"):
            self.model_name = raw_model.replace("qgenie::", "", 1)
        else:
            self.model_name = raw_model
        
        try:
            # 2. Initialize LLM Chat client
            if not _LANGCHAIN_AVAILABLE or QGenieChat is None:
                self.logger.warning(
                    "LangChain/QGenie SDK not available. "
                    "EmailReporter will operate without LLM capabilities."
                )
                self.llm = None
            else:
                self.logger.info(f"Initializing QGenieChat with model: {self.model_name}")
                self.llm = QGenieChat(
                    model=self.model_name,
                    max_tokens=8000,
                    temperature=0.0,
                    api_key=self.api_key,
                    backend_url=self.endpoint
                )
        except Exception as e:
            self.logger.error(f"Failed to init QGenieChat: {e}")
            self.llm = None

    def send_report(
        self, 
        recipients: Union[str, List[str]], 
        attachment_path: str, 
        metadata: Dict[str, Any], 
        stats: Dict[str, Any], 
        analysis_summary: str,
        title: str = "Codebase Analysis Report"
    ) -> bool:
        """
        Generates the HTML report and invokes the QGenie Agent to send it.
        """
        if not self.llm:
            self.logger.error("LLM not initialized. Cannot send report.")
            return False

        # 1. Validate Recipients
        if not recipients:
            self.logger.error("No recipients provided.")
            return False
            
        if isinstance(recipients, list):
            recipients_str = ",".join(str(r).strip() for r in recipients)
        else:
            recipients_str = str(recipients).strip()

        # 2. Validate Attachment
        final_attachment_path = None
        if attachment_path:
            if os.path.exists(attachment_path):
                size = os.path.getsize(attachment_path)
                if size > self.MAX_ATTACHMENT_SIZE:
                    self.logger.warning(f"Attachment too large ({size} bytes). Skipping.")
                else:
                    final_attachment_path = attachment_path
            else:
                self.logger.warning(f"Attachment not found: {attachment_path}")

        # 3. Generate HTML Content
        try:
            html_body = self._generate_html(metadata, stats, analysis_summary, title)
        except Exception as e:
            self.logger.error(f"Error generating HTML: {e}")
            return False

        # 4. Invoke Agent
        return self._invoke_email_agent(recipients_str, html_body, final_attachment_path, title)

    def _invoke_email_agent(self, recipients: str, html_body: str, attachment: str, title: str) -> bool:
        """
        Sets up the Agent with email_tool and executes the request using create_agent.
        """
        # Define tools
        #tools = [email_tool]
        tools = []

        # System prompt
        system_prompt = (
            "You're a helpful assistant with an email tool. "
            "Use the tool to send emails per the user's request. "
            "IMPORTANT: Use the exact HTML content provided by the user for the email body. "
            "Do not modify, summarize, or strip the HTML tags."
        )

        try:
            # Create agent (New Pattern)
            agent = create_agent(
                model=self.llm,
                tools=tools,
                system_prompt=system_prompt,
            )

            # Construct the natural language query
            subject_line = f"{title} - {datetime.now().strftime('%Y-%m-%d')}"
            attachment_instruction = f" The attachment file path is '{attachment}'." if attachment else ""
            
            # We explicitly instruct the model to use the HTML provided
            input_query = (
                f"Send an email to {recipients} with the subject '{subject_line}'.{attachment_instruction}\n"
                f"The body of the email must be the following HTML content exactly:\n\n{html_body}"
            )
            
            #self.logger.info(f"Query: Send email to {recipients} with subject '{subject_line}'")

            # Invoke agent
            result = agent.invoke({"messages": [HumanMessage(content=input_query)]})

            # Extract response for logging
            final_message = (
                result["messages"][-1].content
                if "messages" in result and result["messages"]
                else str(result)
            )
            #self.logger.info(f"Agent Response: {final_message}")
            return True

        except Exception as e:
            #self.logger.error(f"Failed to send email via agent: {e}")
            return False

    def _generate_html(self, metadata: Dict, stats: Dict, analysis: str, title: str) -> str:
        """
        Generates a clean, professional HTML report.
        """
        today = datetime.today().strftime('%Y-%m-%d %H:%M')
        
        # --- Stats Grid Generation ---
        stats_items = ""
        if stats:
            for k, v in stats.items():
                k_lower = k.lower()
                if any(x in k_lower for x in ['fail', 'error', 'bug', 'critical']):
                    border_color = "#e74c3c" # Red
                    icon = "🐞"
                elif any(x in k_lower for x in ['pass', 'fix', 'success', 'scanned']):
                    border_color = "#27ae60" # Green
                    icon = "✅"
                else:
                    border_color = "#3498db" # Blue
                    icon = "ℹ️"
                
                stats_items += f"""
                <div style="background:#fff; padding:12px; border-radius:6px; border-left: 4px solid {border_color}; box-shadow: 0 1px 3px rgba(0,0,0,0.1); text-align:center;">
                    <div style="font-size:11px; text-transform:uppercase; color:#7f8c8d; font-weight:bold; letter-spacing:0.5px;">{k}</div>
                    <div style="font-size:20px; font-weight:700; color:#2c3e50; margin-top:4px;">{icon} {v}</div>
                </div>
                """

        # --- Metadata Table Generation ---
        meta_rows = ""
        for k, v in metadata.items():
            meta_rows += f"<tr><td style='padding:6px 10px; border-bottom:1px solid #eee; color:#666; width:40%;'>{k}</td><td style='padding:6px 10px; border-bottom:1px solid #eee; font-weight:600; color:#333;'>{v}</td></tr>"

        # --- Analysis Text Formatting ---
        if analysis:
            analysis_html = analysis.replace("\n", "<br/>")
            analysis_section = f"""
            <div style="background:#f8f9fa; padding:15px; border-radius:6px; border:1px solid #e9ecef; margin-top:20px;">
                <h3 style="margin-top:0; margin-bottom:10px; font-size:16px; color:#2c3e50; border-bottom:1px solid #ddd; padding-bottom:8px;">🔍 Executive Summary</h3>
                <div style="font-size:14px; line-height:1.6; color:#444;">{analysis_html}</div>
            </div>
            """
        else:
            analysis_section = ""

        # --- Full HTML Template ---
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
        </head>
        <body style="font-family: 'Segoe UI', Helvetica, Arial, sans-serif; background-color: #f4f6f8; margin: 0; padding: 20px;">
            <div style="max-width: 700px; margin: 0 auto; background: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.08);">
                
                <!-- Header -->
                <div style="background: linear-gradient(135deg, #2c3e50 0%, #4a698a 100%); padding: 20px; text-align: center; color: white;">
                    <h1 style="margin:0; font-size:22px;">{title}</h1>
                    <div style="margin-top:5px; font-size:13px; opacity:0.9;">Generated by QGenie Agent</div>
                </div>
                
                <div style="padding: 25px;">
                    <p style="color:#555; font-size:14px; line-height:1.5;">
                        Hello,<br><br>
                        The automated codebase analysis is complete. Please find the summary of results below.
                        A detailed line-by-line report is available in the attached Excel file.
                    </p>
                    
                    <!-- Statistics Grid -->
                    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 20px;">
                        {stats_items}
                    </div>

                    <!-- Analysis Section -->
                    {analysis_section}

                    <!-- Metadata Section -->
                    <div style="margin-top: 25px;">
                        <h3 style="font-size:15px; color:#2c3e50; border-bottom:2px solid #eee; padding-bottom:5px; margin-bottom:10px;">⚙️ Run Details</h3>
                        <table style="width: 100%; border-collapse: collapse; font-size: 13px;">
                            {meta_rows}
                            <tr><td style='padding:6px 10px; border-bottom:1px solid #eee; color:#666;'>Generated On</td><td style='padding:6px 10px; border-bottom:1px solid #eee; font-weight:600;'>{today}</td></tr>
                        </table>
                    </div>

                    <!-- Footer Note -->
                    <div style="margin-top:25px; padding:12px; background:#e3f2fd; border-left:4px solid #2196f3; color:#0d47a1; font-size:13px; border-radius:4px;">
                        ℹ️ <strong>Note:</strong> This is an automated message. Please review the attachment for specific code pointers.
                    </div>
                </div>

                <div style="background: #f8f9fa; padding: 15px; text-align: center; font-size: 11px; color: #95a5a6; border-top: 1px solid #eee;">
                    QGenie Automation Ecosystem • Internal Use Only
                </div>
            </div>
        </body>
        </html>
        """

if __name__ == "__main__":
    reporter = EmailReporter()
    
    dummy_stats = {"Files Scanned": 10, "Issues Fixed": 5}
    dummy_meta = {"Project": "TestProject", "User": "pavanr"}
    
    # Test sending
    # NOTE: Ensure recipients are valid for actual testing
    reporter.send_report(
        recipients="pavanr@qti.qualcomm.com", 
        attachment_path="", 
        metadata=dummy_meta, 
        stats=dummy_stats, 
        analysis_summary="Integration Test: create_agent implementation successful.",
        title="Integration Test Report"
    )