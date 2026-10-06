"""Service for generating meeting reports (PDF and CSV) and analytics."""

import csv
import io
from typing import Literal, Dict, Any, Union, Optional
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

from app.repositories.meeting_repository import MeetingRepository
from app.repositories.intelligence_repository import IntelligenceRepository
from app.schemas.report import AnalyticsResponse

ExportFormat = Literal["pdf", "csv"]

class ReportService:
    def __init__(self, meetings: Optional[MeetingRepository] = None, intelligence: Optional[IntelligenceRepository] = None):
        self._meetings = meetings or MeetingRepository()
        self._intelligence = intelligence or IntelligenceRepository()

    def generate_report(self, meeting_id: str, export_format: ExportFormat) -> Union[bytes, str]:
        meeting = self._meetings.get_or_404(meeting_id)
        intel = self._intelligence.get_intelligence(meeting_id)

        if export_format == "pdf":
            return self._generate_pdf(meeting, intel)
        elif export_format == "csv":
            return self._generate_csv(meeting, intel)
        else:
            raise ValueError(f"Unsupported format: {export_format}")

    def get_analytics(self, meeting_id: str) -> AnalyticsResponse:
        meeting = self._meetings.get_or_404(meeting_id)
        intel = self._intelligence.get_intelligence(meeting_id)

        duration = meeting.get("duration_seconds")
        word_count = meeting.get("transcript_word_count")
        
        participant_count = 0
        action_item_count = 0
        completed_action_items = 0
        pending_action_items = 0
        decision_count = 0
        key_point_count = 0
        deadline_count = 0
        action_items_by_participant: Dict[str, int] = {}
        priority_distribution: Dict[str, int] = {}

        if intel:
            participant_count = len(intel.get("participants", []))
            decisions = intel.get("decisions", [])
            decision_count = len(decisions)
            key_points = intel.get("key_points", [])
            key_point_count = len(key_points)
            
            action_items = intel.get("action_items", [])
            action_item_count = len(action_items)
            
            for item in action_items:
                status = item.get("status", "").lower()
                if status in ("completed", "done"):
                    completed_action_items += 1
                else:
                    pending_action_items += 1
                
                if item.get("deadline"):
                    deadline_count += 1
                    
                assignee = item.get("assigned_to") or "Unassigned"
                action_items_by_participant[assignee] = action_items_by_participant.get(assignee, 0) + 1
                
                priority = item.get("priority") or "medium"
                priority_distribution[priority] = priority_distribution.get(priority, 0) + 1

        return AnalyticsResponse(
            meeting_duration_seconds=duration,
            participant_count=participant_count,
            action_item_count=action_item_count,
            completed_action_items=completed_action_items,
            pending_action_items=pending_action_items,
            decision_count=decision_count,
            key_point_count=key_point_count,
            deadline_count=deadline_count,
            action_items_by_participant=action_items_by_participant,
            priority_distribution=priority_distribution,
            transcript_word_count=word_count,
            index_status="indexed" if intel else "unindexed"
        )

    def _generate_pdf(self, meeting: Dict[str, Any], intel: Optional[Dict[str, Any]]) -> bytes:
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter)
        styles = getSampleStyleSheet()
        story = []

        title = meeting.get("title") or "Meeting Report"
        story.append(Paragraph(title, styles['Title']))
        story.append(Spacer(1, 12))
        
        created_at = meeting.get("created_at") or "Unknown Date"
        duration = meeting.get("duration_seconds") or 0
        
        story.append(Paragraph(f"Date: {created_at}", styles['Normal']))
        story.append(Paragraph(f"Duration: {duration} seconds", styles['Normal']))
        story.append(Spacer(1, 12))

        if intel:
            story.append(Paragraph("Summary", styles['Heading2']))
            story.append(Paragraph(intel.get("summary") or "", styles['Normal']))
            story.append(Spacer(1, 12))
            
            story.append(Paragraph("Key Decisions", styles['Heading2']))
            for d in intel.get("decisions", []):
                story.append(Paragraph(f"• {d.get('text', '')}", styles['Normal']))
            story.append(Spacer(1, 12))
            
            story.append(Paragraph("Action Items", styles['Heading2']))
            for a in intel.get("action_items", []):
                task = a.get("task", "")
                assignee = a.get("assigned_to") or "Unassigned"
                deadline = a.get("deadline") or "None"
                priority = a.get("priority") or "medium"
                status = a.get("status") or "pending"
                story.append(Paragraph(f"• {task} (Assignee: {assignee}, Deadline: {deadline}, Priority: {priority}, Status: {status})", styles['Normal']))
            story.append(Spacer(1, 12))
            
            story.append(Paragraph("Participants", styles['Heading2']))
            for p in intel.get("participants", []):
                name = p.get("name", "")
                role = p.get("role") or "Participant"
                story.append(Paragraph(f"• {name} - {role}", styles['Normal']))
            story.append(Spacer(1, 12))

        doc.build(story)
        return buffer.getvalue()

    def _generate_csv(self, meeting: Dict[str, Any], intel: Optional[Dict[str, Any]]) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(["Meeting Title", "Date", "Type", "Content", "Assigned To", "Deadline", "Priority", "Status"])
        
        title = meeting.get("title") or "Untitled"
        date = meeting.get("created_at") or ""

        if intel:
            for d in intel.get("decisions", []):
                writer.writerow([title, date, "Decision", d.get("text", ""), "", "", "", ""])
                
            for k in intel.get("key_points", []):
                writer.writerow([title, date, "Key Point", k.get("text", ""), "", "", "", ""])
                
            for a in intel.get("action_items", []):
                writer.writerow([
                    title, 
                    date, 
                    "Action Item", 
                    a.get("task", ""), 
                    a.get("assigned_to", ""), 
                    a.get("deadline", ""), 
                    a.get("priority", ""), 
                    a.get("status", "")
                ])
                
        return buffer.getvalue()
