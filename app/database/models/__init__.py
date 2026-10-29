# makes models/ a package
# Explicit imports ensure all models are registered with Base.metadata
# so Alembic autogenerate and test setup can discover them.
from app.database.models.document import Document  # noqa: F401
from app.database.models.folder import Folder  # noqa: F401
from app.database.models.quiz import Quiz, Question  # noqa: F401
from app.database.models.attempt import QuizAttempt, AttemptAnswer  # noqa: F401
from app.database.models.study import (  # noqa: F401
    TopicPerformance,
    ReviewItem,
    ReviewSession,
    ReviewSessionItem,
    StudyGoal,
    StudyActivity,
)
from app.database.models.flashcard import (  # noqa: F401
    StudySet,
    StudySetSourceDocument,
    Flashcard,
    FlashcardReviewState,
    FlashcardReviewSession,
    FlashcardReviewSessionItem,
    FlashcardReviewResult,
)
from app.database.models.chat import (  # noqa: F401
    ChatSession,
    ChatSessionDocument,
    ChatMessage,
    ChatCitation,
    ChatGenerationRecord,
)
from app.database.models.auth import User, RefreshToken
from app.database.models.job import ProcessingJob, WorkerHeartbeat  # noqa: F401
from app.database.models.social import (Post, PostAllowedUser, PostMedia, PostReaction, PostComment, SavedPost)  # noqa: F401
from app.database.models.moderation import ContentReport, ModerationAction, UserRestriction  # noqa: F401
from app.database.models.study_guide import (  # noqa: F401
    StudyGuide,
    StudyGuideSection,
    StudyGuideSourceDocument,
)
