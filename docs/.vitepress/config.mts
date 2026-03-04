import { defineConfig } from 'vitepress'
import { withMermaid } from 'vitepress-plugin-mermaid'

const reportPath = '/report'

export default withMermaid(
    defineConfig({
        base: '/SmartGym-Monitor',
        title: 'SmartGym-Monitor',
        description: 'A gym monitor system',
        themeConfig: {
            nav: [
                { text: 'Home', link: '/' },
            ],
            sidebar: [
                {
                    text: 'Report',
                    items: [
                        { text: 'Introduction', link: `${reportPath}/Introduction` },
                        { text: 'Work Plan', link: `${reportPath}/WorkPlan` },
                        { text: 'Requirements Analisys', link: `${reportPath}/Requirements` },
                        { text: 'Design', link: `${reportPath}/Design` },
                        { text: 'Implementation', link: `${reportPath}/Implementation` },
                        { text: 'Technologies', link: `${reportPath}/Technologies` },
                        { text: 'DevOps', link: `${reportPath}/DevOps` },
                        { text: 'Conclusion', link: `${reportPath}/Conclusion` },
                    ]
                },
            ],
            socialLinks: [
                { icon: 'github', link: 'https://github.com/piertv21/SmartGym-Monitor' }
            ]
        }
    })
)