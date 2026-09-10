/**
 * Shared type definitions.
 *
 * The project uses JavaScript with JSDoc typedefs rather than TypeScript: the
 * editor still gives autocomplete and type checking on these shapes, without
 * adding a compile step to the build. Field names match the backend JSON
 * exactly (snake_case), so there is no silent renaming layer between the API
 * and the UI.
 */

/**
 * @typedef {'UPLOADED'|'VALIDATING'|'AUDIO_PROCESSING'|'TRANSCRIBING'|
 *   'TRANSCRIPT_VALIDATED'|'AI_ANALYSIS'|'PERSISTING'|'COMPLETED'|'FAILED'} ProcessingStatus
 */

/**
 * @typedef {Object} Meeting
 * @property {string} id
 * @property {string} title
 * @property {string} original_filename
 * @property {'audio'|'video'} media_kind
 * @property {number} file_size_bytes
 * @property {number|null} duration_seconds
 * @property {ProcessingStatus} status
 * @property {boolean} has_transcript
 * @property {boolean} has_intelligence
 * @property {string|null} error_code
 * @property {string|null} error_message
 * @property {string|null} created_at
 * @property {number} [segment_count]
 * @property {number|null} [transcript_word_count]
 * @property {string|null} [transcript_language]
 * @property {string|null} [transcript_model]
 */

/**
 * @typedef {Object} TranscriptSegment
 * @property {number} segment_index
 * @property {number} start_time
 * @property {number} end_time
 * @property {string} text
 * @property {string|null} speaker
 * @property {number|null} confidence
 */

/**
 * @typedef {Object} Transcript
 * @property {string} meeting_id
 * @property {string} text
 * @property {TranscriptSegment[]} segments
 * @property {string|null} language
 * @property {number|null} duration_seconds
 * @property {string|null} model
 * @property {number} word_count
 */

/**
 * @typedef {Object} ActionItem
 * @property {string|null} id
 * @property {string} task
 * @property {string|null} assigned_to  Null when the transcript never says.
 * @property {string|null} deadline     Null when not stated; kept as spoken.
 * @property {'high'|'medium'|'low'} priority
 * @property {'pending'|'in_progress'|'completed'|'blocked'} status
 * @property {string|null} context
 */

/**
 * @typedef {Object} Participant
 * @property {string|null} id
 * @property {string} name
 * @property {string|null} normalized_name
 * @property {string|null} role
 * @property {boolean} is_unknown
 * @property {number} mention_count
 * @property {string[]} aliases
 */

/**
 * @typedef {Object} MeetingIntelligence
 * @property {string} meeting_id
 * @property {string} summary
 * @property {{id: string|null, text: string}[]} key_points
 * @property {{id: string|null, text: string, context: string|null}[]} decisions
 * @property {Participant[]} participants
 * @property {ActionItem[]} action_items
 * @property {string|null} model
 * @property {'grok'|'gemini'|'groq'|null} provider  Which AI provider answered.
 * @property {number} chunk_count
 */

/**
 * @typedef {Object} AccuracyResult
 * @property {number} accuracy_percentage
 * @property {number} word_error_rate
 * @property {number} reference_word_count
 * @property {number} hypothesis_word_count
 * @property {number} correct_words
 * @property {number} substitutions
 * @property {number} deletions
 * @property {number} insertions
 * @property {string[]} missing_words
 * @property {string[]} extra_words
 * @property {{reference_word: string, hypothesis_word: string}[]} incorrect_words
 * @property {number} target_accuracy
 * @property {boolean} passed
 * @property {'PASSED'|'NEEDS IMPROVEMENT'} status
 * @property {string} explanation
 */

export {};
