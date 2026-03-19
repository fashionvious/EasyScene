import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/_layout/text2video')({
  component: RouteComponent,
})

function RouteComponent() {
  return <div>Hello "/_layout/text2video"!</div>
}
