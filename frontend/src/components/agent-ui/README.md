# Agent UI Integration

Coworker now uses shadcn/ui generated components for general UI primitives and chat message structure. This keeps components local and themeable while avoiding fully hand-written menus, selects, and message primitives.

## Current Library Boundary

- `src/components/ui/select.tsx`: shadcn/Radix select, used by the model picker.
- `src/components/ui/dropdown-menu.tsx`: shadcn/Radix dropdown menu, used by provider actions.
- `src/components/ui/message.tsx`: shadcn message primitives, used by the chat timeline.
- Existing Coworker theme variables remain the single source of visual truth.

## assistant-ui Next Step

assistant-ui is a stronger fit when Coworker moves thread/message state out of `App.tsx` and into an assistant runtime. The likely path is:

1. Add `@assistant-ui/react`.
2. Implement a `ChatModelAdapter` that calls `chatService.sendMessage`.
3. Wrap the chat surface with `AssistantRuntimeProvider`.
4. Replace `MessageList` and `ChatInput` with assistant-ui `ThreadPrimitive` and `ComposerPrimitive` composition.

Do not add assistant-ui as a passive dependency until the runtime migration starts.
