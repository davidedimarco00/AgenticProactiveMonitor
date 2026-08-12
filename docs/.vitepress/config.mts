import { defineConfig } from 'vitepress'
import { withMermaid } from 'vitepress-plugin-mermaid'

const reportPath = '/report'

export default withMermaid(
  defineConfig({
    base: '/AgenticProactiveMonitor/',
    title: 'Agentic Proactive Monitor',
    description: 'Hybrid multi-agent system for proactive infrastructure monitoring, anomaly detection, and explainable diagnosis.',
    themeConfig: {
      nav: [
        { text: 'Home', link: '/' },
        { text: 'Report', link: `${reportPath}/Introduction` },
      ],
      sidebar: [
        {
          text: 'Documentation',
          items: [
            { text: 'Introduction', link: `${reportPath}/Introduction` },
            { text: 'Work Plan', link: `${reportPath}/WorkPlan` },
            { text: 'Requirements Analysis', link: `${reportPath}/Requirements` },
            { text: 'Design', link: `${reportPath}/Design` },
            { text: 'Implementation', link: `${reportPath}/Implementation` },
            { text: 'Technologies', link: `${reportPath}/Technologies` },
            { text: 'DevOps', link: `${reportPath}/DevOps` },
            { text: 'Conclusion', link: `${reportPath}/Conclusion` },
          ],
        },
      ],
      socialLinks: [
        {
          icon: 'github',
          link: 'https://github.com/davidedimarco00/AgenticProactiveMonitor',
        },
      ],
    },
  }),
)
