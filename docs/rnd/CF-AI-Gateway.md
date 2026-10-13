The official starter template for building AI applications with Cloudflare AI Gateway and Workers AI is the cloudflare/agents-starter, which integrates the Agents SDK, Workers AI, streaming, and real-time WebSockets. [1] (https://github.com/cloudflare/agents-starter)

Key Starter Templates & Options

• Cloudflare Agents Starter: A full-featured starter kit for building AI chat agents on Cloudflare using the Agents SDK and Workers AI without third-party API keys. It features AI streaming via AIChatAgent, vision input, real-time WebSockets, Kumo UI, and Model Context Protocol (MCP) server support. [1] (https://github.com/cloudflare/agents-starter)
• Workers AI with AI Gateway Binding: A minimal TypeScript Cloudflare Worker setup. Run npm create cloudflare@latest -- hello-ai and bind ai: { binding: "AI" } inside your wrangler.jsonc or wrangler.toml file to route model requests directly through the default gateway: [1] (https://blog.cloudflare.com/workers-ai-gateway-unification/), [2] (https://developers.cloudflare.com/ai-gateway/integrations/aig-workers-ai-binding/)
javascript
const response = await env.AI.run(
  '@cf/meta/llama-3.1-8b-instruct-fast',
  { messages: [{ role: 'user', content: 'Hello!' }] },
  { gateway: { id: 'default' } }
);
Use code with caution.

Quick Setup for Automatic AI Gateway

You can start using Cloudflare AI Gateway automatically with zero manual gateway creation by routing through the default ID: [1] (https://developers.cloudflare.com/changelog/post/2026-03-02-default-gateway/)
• Endpoint: https://gateway.ai.cloudflare.com/v1/$CLOUDFLARE_ACCOUNT_ID/default/compat/chat/completions [1] (https://developers.cloudflare.com/changelog/post/2026-03-02-default-gateway/)
• Required Headers:
	• cf-aig-authorization: Bearer $CLOUDFLARE_API_TOKEN
	• Content-Type: application/json [1] (https://developers.cloudflare.com/changelog/post/2026-03-02-default-gateway/)
Would you like help with:
• Setting up a custom domain for your gateway?
• Configuring Bring Your Own Keys (BYOK) for third-party providers like OpenAI or Anthropic?
• Writing a custom Worker deployment script?
