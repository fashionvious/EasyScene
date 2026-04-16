"""
Skill 解析器 - 自动读取 jianying-editor-skill 目录并拼装成 Skill 对象
"""
import os
import re
from typing import TypedDict, Optional
from pathlib import Path


class Skill(TypedDict):
    """技能对象，用于渐进式披露给 Agent"""
    name: str  # 技能名称，用于识别和加载
    description: str  # 技能描述，帮助 LLM 决定是否需要此技能
    content: str  # 完整内容，仅在需要时加载
    category: str  # 分类：main, rule, script, example


class SkillParser:
    """解析 jianying-editor-skill 目录，自动生成 Skill 对象"""
    
    def __init__(self, skill_root: str):
        """
        初始化解析器
        
        Args:
            skill_root: jianying-editor-skill 的根目录路径
        """
        self.skill_root = Path(skill_root)
        self.skills: list[Skill] = []
        
    def parse_all(self) -> list[Skill]:
        """解析所有技能文件"""
        self.skills = []
        
        # 1. 解析主技能文件 SKILL.md
        self._parse_main_skill()
        
        # 2. 解析 rules 目录下的规则文件
        self._parse_rules()
        
        # 3. 解析 scripts 目录下的脚本
        self._parse_scripts()
        
        # 4. 解析 examples 目录下的示例
        self._parse_examples()
        
        return self.skills
    
    def _parse_main_skill(self) -> None:
        """解析主技能文件 SKILL.md"""
        skill_file = self.skill_root / "SKILL.md"
        if not skill_file.exists():
            return
            
        content = skill_file.read_text(encoding="utf-8")
        
        # 从文件头部提取 name 和 description
        name = "jianying-editor"
        description = "剪映 AI自动化剪辑的高级封装 API"
        
        # 解析 YAML frontmatter
        frontmatter_match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
        if frontmatter_match:
            frontmatter = frontmatter_match.group(1)
            name_match = re.search(r'name:\s*(.+)', frontmatter)
            desc_match = re.search(r'description:\s*(.+)', frontmatter)
            
            if name_match:
                name = name_match.group(1).strip()
            if desc_match:
                description = desc_match.group(1).strip()
        
        self.skills.append({
            "name": name,
            "description": description,
            "content": content,
            "category": "main"
        })
    
    def _parse_rules(self) -> None:
        """解析 rules 目录下的规则文件"""
        rules_dir = self.skill_root / "rules"
        if not rules_dir.exists():
            return
            
        for rule_file in rules_dir.glob("*.md"):
            content = rule_file.read_text(encoding="utf-8")
            
            # 从文件名和内容提取信息
            name = f"rule_{rule_file.stem}"
            description = f"规则: {rule_file.stem}"
            
            # 解析 YAML frontmatter
            frontmatter_match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
            if frontmatter_match:
                frontmatter = frontmatter_match.group(1)
                name_match = re.search(r'name:\s*(.+)', frontmatter)
                desc_match = re.search(r'description:\s*(.+)', frontmatter)
                
                if name_match:
                    name = name_match.group(1).strip()
                if desc_match:
                    description = desc_match.group(1).strip()
            
            self.skills.append({
                "name": name,
                "description": description,
                "content": content,
                "category": "rule"
            })
    
    def _parse_scripts(self) -> None:
        """解析 scripts 目录下的脚本文件"""
        scripts_dir = self.skill_root / "scripts"
        if not scripts_dir.exists():
            return
            
        # 只解析顶层脚本，忽略子目录
        for script_file in scripts_dir.glob("*.py"):
            if script_file.name.startswith("_"):
                continue
                
            content = script_file.read_text(encoding="utf-8")
            
            # 从文件名生成技能名称
            name = f"script_{script_file.stem}"
            
            # 提取脚本的 docstring 作为描述
            description = f"脚本: {script_file.stem}"
            docstring_match = re.search(r'"""(.*?)"""', content, re.DOTALL)
            if docstring_match:
                docstring = docstring_match.group(1).strip()
                # 只取第一行作为描述
                description = docstring.split('\n')[0].strip()
            
            self.skills.append({
                "name": name,
                "description": description,
                "content": content,
                "category": "script"
            })
    
    def _parse_examples(self) -> None:
        """解析 examples 目录下的示例文件"""
        examples_dir = self.skill_root / "examples"
        if not examples_dir.exists():
            return
            
        for example_file in examples_dir.glob("*.py"):
            if example_file.name.startswith("_"):
                continue
                
            content = example_file.read_text(encoding="utf-8")
            
            # 从文件名生成技能名称
            name = f"example_{example_file.stem}"
            
            # 提取示例的 docstring 作为描述
            description = f"示例: {example_file.stem}"
            docstring_match = re.search(r'"""(.*?)"""', content, re.DOTALL)
            if docstring_match:
                docstring = docstring_match.group(1).strip()
                description = docstring.split('\n')[0].strip()
            
            self.skills.append({
                "name": name,
                "description": description,
                "content": content,
                "category": "example"
            })
    
    def get_skill(self, skill_name: str) -> Optional[Skill]:
        """根据名称获取技能"""
        for skill in self.skills:
            if skill["name"] == skill_name:
                return skill
        return None
    
    def get_skills_by_category(self, category: str) -> list[Skill]:
        """根据分类获取技能列表"""
        return [s for s in self.skills if s["category"] == category]
    
    def get_skill_names(self) -> list[str]:
        """获取所有技能名称"""
        return [s["name"] for s in self.skills]


def load_jianying_skill(skill_root: str) -> list[Skill]:
    """
    加载 jianying-editor-skill 的便捷函数
    
    Args:
        skill_root: jianying-editor-skill 的根目录路径
        
    Returns:
        技能列表
    """
    parser = SkillParser(skill_root)
    return parser.parse_all()
